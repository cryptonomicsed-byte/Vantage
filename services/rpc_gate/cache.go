// Immutable-read cache (LRU + TTL), cacheability rules, and in-flight
// request coalescing. Standard library only (no x/sync/singleflight).
package main

import (
	"bytes"
	"container/list"
	"encoding/json"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type cacheEntry struct {
	key    string
	result json.RawMessage
	exp    time.Time
}

type lruCache struct {
	mu      sync.Mutex
	cap     int
	ttl     time.Duration
	ll      *list.List
	m       map[string]*list.Element
	now     func() time.Time
	hits    atomic.Int64
	misses  atomic.Int64
	maxSize int
}

func newLRU(capacity int, ttl time.Duration, maxEntryBytes int) *lruCache {
	return &lruCache{cap: capacity, ttl: ttl, ll: list.New(), m: map[string]*list.Element{},
		now: time.Now, maxSize: maxEntryBytes}
}

func (c *lruCache) get(k string) (json.RawMessage, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if el, ok := c.m[k]; ok {
		e := el.Value.(*cacheEntry)
		if c.now().Before(e.exp) {
			c.ll.MoveToFront(el)
			c.hits.Add(1)
			return e.result, true
		}
		c.ll.Remove(el)
		delete(c.m, k)
	}
	c.misses.Add(1)
	return nil, false
}

func (c *lruCache) put(k string, v json.RawMessage) {
	if c.cap <= 0 || (c.maxSize > 0 && len(v) > c.maxSize) {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	exp := c.now().Add(c.ttl)
	if el, ok := c.m[k]; ok {
		e := el.Value.(*cacheEntry)
		e.result, e.exp = v, exp
		c.ll.MoveToFront(el)
		return
	}
	c.m[k] = c.ll.PushFront(&cacheEntry{key: k, result: v, exp: exp})
	for c.ll.Len() > c.cap {
		old := c.ll.Back()
		c.ll.Remove(old)
		delete(c.m, old.Value.(*cacheEntry).key)
	}
}

func (c *lruCache) size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.ll.Len()
}

// ---- cacheability --------------------------------------------------------

func decodeParams(raw json.RawMessage) ([]any, bool) {
	if len(bytes.TrimSpace(raw)) == 0 {
		return nil, true
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var p []any
	if err := d.Decode(&p); err != nil {
		return nil, false
	}
	return p, true
}

// finalizedOrDefault: no config, or a config whose commitment is absent/"finalized".
// (Solana RPC's default commitment is finalized.) A bare string second param
// is the legacy encoding form (e.g. getTransaction(sig, "json")) -> ok.
func finalizedOrDefault(p []any, idx int) (map[string]any, bool) {
	if len(p) <= idx || p[idx] == nil {
		return nil, true
	}
	switch v := p[idx].(type) {
	case string:
		return nil, true
	case map[string]any:
		c, has := v["commitment"]
		if !has || c == nil {
			return v, true
		}
		s, _ := c.(string)
		return v, s == "finalized"
	}
	return nil, false
}

// cacheKeyFor returns a canonical key (method + re-marshalled params, map keys
// sorted, numbers kept exact) if the request is eligible for caching.
func cacheKeyFor(method string, params json.RawMessage) (string, bool) {
	p, ok := decodeParams(params)
	if !ok || len(p) == 0 {
		return "", false
	}
	switch method {
	case "getTransaction":
		if s, _ := p[0].(string); s == "" {
			return "", false
		}
		if _, ok := finalizedOrDefault(p, 1); !ok {
			return "", false
		}
	case "getBlock":
		if _, isNum := p[0].(json.Number); !isNum {
			return "", false
		}
		if _, ok := finalizedOrDefault(p, 1); !ok {
			return "", false
		}
	case "getBlockTime":
		if _, isNum := p[0].(json.Number); !isNum || len(p) != 1 {
			return "", false
		}
	case "getSignaturesForAddress":
		if s, _ := p[0].(string); s == "" {
			return "", false
		}
		cfg, ok := finalizedOrDefault(p, 1)
		if !ok || cfg == nil {
			return "", false
		}
		if b, _ := cfg["before"].(string); b == "" {
			return "", false // newest page is mutable; only `before` pages are immutable
		}
	default:
		return "", false
	}
	canon, err := json.Marshal(p)
	if err != nil {
		return "", false
	}
	return method + "|" + string(canon), true
}

// storableResult extracts the result from a successful single JSON-RPC
// response if it is safe to cache. finalAge guards getBlockTime: a block
// whose timestamp is older than finalAge is long past finalization (~13s).
func storableResult(method string, obj map[string]json.RawMessage, now time.Time, finalAge time.Duration) (json.RawMessage, bool) {
	if e, has := obj["error"]; has && !isNull(e) {
		return nil, false
	}
	res, has := obj["result"]
	if !has || isNull(res) {
		return nil, false // e.g. getTransaction not found yet -- not immutable
	}
	switch method {
	case "getBlockTime":
		var ts int64
		if err := json.Unmarshal(res, &ts); err != nil {
			return nil, false
		}
		if time.Unix(ts, 0).After(now.Add(-finalAge)) {
			return nil, false
		}
	case "getSignaturesForAddress":
		var rows []struct {
			ConfirmationStatus *string `json:"confirmationStatus"`
		}
		if err := json.Unmarshal(res, &rows); err != nil || len(rows) == 0 {
			return nil, false
		}
		for _, r := range rows {
			if r.ConfirmationStatus != nil && *r.ConfirmationStatus != "finalized" {
				return nil, false
			}
		}
	}
	return res, true
}

func isNull(r json.RawMessage) bool {
	return strings.TrimSpace(string(r)) == "null"
}

// ---- coalescing ----------------------------------------------------------

type flightCall struct {
	wg  sync.WaitGroup
	res upstreamResult
}

type flightGroup struct {
	mu        sync.Mutex
	m         map[string]*flightCall
	coalesced atomic.Int64
}

// do runs fn once per key among concurrent callers. leader reports whether
// this caller ran fn (and so owns the verbatim response).
func (g *flightGroup) do(key string, fn func() upstreamResult) (res upstreamResult, leader bool) {
	g.mu.Lock()
	if g.m == nil {
		g.m = map[string]*flightCall{}
	}
	if c, ok := g.m[key]; ok {
		g.mu.Unlock()
		g.coalesced.Add(1)
		c.wg.Wait()
		return c.res, false
	}
	c := &flightCall{}
	c.wg.Add(1)
	g.m[key] = c
	g.mu.Unlock()

	defer func() {
		c.wg.Done()
		g.mu.Lock()
		delete(g.m, key)
		g.mu.Unlock()
	}()
	c.res = fn()
	return c.res, true
}
