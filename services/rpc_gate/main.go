// ares-rpc-gate -- fleet Solana JSON-RPC gateway (Go, stdlib only).
//
// Rotates the fleet's Helius keys round-robin using the SAME shared cooldown
// state as /opt/ares/api_key_pool.py (see pool.go), falls back to the public
// RPC behind a token bucket when every key is cooling, caches immutable reads
// and coalesces identical in-flight cacheable requests (see cache.go).
//
// Endpoints:
//
//	POST /  and  POST /solana   JSON-RPC 2.0 (single or batch), upstream body returned verbatim
//	GET  /health                pool/caching/upstream counters, keys masked to last 4 chars
//
// Env (all optional):
//
//	RPC_GATE_ADDR               listen address (default 127.0.0.1:8092; non-loopback requires RPC_GATE_TOKEN)
//	RPC_GATE_TOKEN              bearer token ("Authorization: Bearer <t>" or ?token=<t>); unset = no auth
//	RPC_GATE_POOL_CONFIG        default /opt/ares/.api_key_pools.json
//	RPC_GATE_POOL_STATE         default /opt/ares/.api_key_pool_state.json
//	RPC_GATE_PROVIDER           pool provider name (default helius)
//	HELIUS_RPC_BASE             default https://mainnet.helius-rpc.com/  (key sent as ?api-key=)
//	SOLANA_PUBLIC_RPC           default https://api.mainnet-beta.solana.com
//	PUBLIC_RPS                  public fallback token-bucket rate (default 4; 0 disables fallback)
//	PUBLIC_BURST                bucket size (default = ceil(PUBLIC_RPS))
//	PUBLIC_WAIT_MS              max time a request queues for a public token (default 3000)
//	RPC_GATE_CACHE_SIZE         LRU entries (default 5000; 0 disables cache)
//	RPC_GATE_CACHE_TTL_S        entry TTL seconds (default 600)
//	RPC_GATE_CACHE_MAX_ENTRY    max cached result bytes (default 1048576)
//	RPC_GATE_UPSTREAM_TIMEOUT_S per upstream call (default 30)
//	RPC_GATE_RELOAD_MS          min interval between config/state mtime checks (default 1000)
package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Config struct {
	Addr            string
	Token           string
	PoolConfig      string
	PoolState       string
	Provider        string
	HeliusBase      string
	PublicRPC       string
	PublicRPS       float64
	PublicBurst     int
	PublicWait      time.Duration
	CacheSize       int
	CacheTTL        time.Duration
	CacheMaxEntry   int
	UpstreamTimeout time.Duration
	ReloadEvery     time.Duration
	FinalityAge     time.Duration
	MaxRequestBytes int64
	MaxRespBytes    int64
}

func envStr(k, def string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(strings.TrimSpace(os.Getenv(k)), 64); err == nil && v >= 0 {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v, err := strconv.Atoi(strings.TrimSpace(os.Getenv(k))); err == nil && v >= 0 {
		return v
	}
	return def
}

func configFromEnv() Config {
	rps := envFloat("PUBLIC_RPS", 4)
	return Config{
		Addr:            envStr("RPC_GATE_ADDR", "127.0.0.1:8092"),
		Token:           os.Getenv("RPC_GATE_TOKEN"),
		PoolConfig:      envStr("RPC_GATE_POOL_CONFIG", "/opt/ares/.api_key_pools.json"),
		PoolState:       envStr("RPC_GATE_POOL_STATE", "/opt/ares/.api_key_pool_state.json"),
		Provider:        envStr("RPC_GATE_PROVIDER", "helius"),
		HeliusBase:      envStr("HELIUS_RPC_BASE", "https://mainnet.helius-rpc.com/"),
		PublicRPC:       envStr("SOLANA_PUBLIC_RPC", "https://api.mainnet-beta.solana.com"),
		PublicRPS:       rps,
		PublicBurst:     envInt("PUBLIC_BURST", int(math.Max(1, math.Ceil(rps)))),
		PublicWait:      time.Duration(envInt("PUBLIC_WAIT_MS", 3000)) * time.Millisecond,
		CacheSize:       envInt("RPC_GATE_CACHE_SIZE", 5000),
		CacheTTL:        time.Duration(envInt("RPC_GATE_CACHE_TTL_S", 600)) * time.Second,
		CacheMaxEntry:   envInt("RPC_GATE_CACHE_MAX_ENTRY", 1<<20),
		UpstreamTimeout: time.Duration(envInt("RPC_GATE_UPSTREAM_TIMEOUT_S", 30)) * time.Second,
		ReloadEvery:     time.Duration(envInt("RPC_GATE_RELOAD_MS", 1000)) * time.Millisecond,
		FinalityAge:     2 * time.Minute,
		MaxRequestBytes: 4 << 20,
		MaxRespBytes:    64 << 20,
	}
}

// ---- token bucket --------------------------------------------------------

type tokenBucket struct {
	mu     sync.Mutex
	rate   float64
	burst  float64
	tokens float64
	last   time.Time
}

func newBucket(rate float64, burst int) *tokenBucket {
	if burst < 1 {
		burst = 1
	}
	return &tokenBucket{rate: rate, burst: float64(burst), tokens: float64(burst), last: time.Now()}
}

func (b *tokenBucket) wait(ctx context.Context) error {
	if b.rate <= 0 {
		return errors.New("public fallback disabled (PUBLIC_RPS=0)")
	}
	for {
		b.mu.Lock()
		now := time.Now()
		b.tokens = math.Min(b.burst, b.tokens+now.Sub(b.last).Seconds()*b.rate)
		b.last = now
		if b.tokens >= 1 {
			b.tokens--
			b.mu.Unlock()
			return nil
		}
		need := time.Duration((1 - b.tokens) / b.rate * float64(time.Second))
		b.mu.Unlock()
		if dl, ok := ctx.Deadline(); ok && time.Until(dl) < need {
			return errors.New("public fallback rate limit")
		}
		t := time.NewTimer(need)
		select {
		case <-ctx.Done():
			t.Stop()
			return errors.New("public fallback rate limit")
		case <-t.C:
		}
	}
}

// ---- gateway -------------------------------------------------------------

type upstreamResult struct {
	status int
	body   []byte
	ctype  string
}

type Gate struct {
	cfg    Config
	pool   *keyPool
	cache  *lruCache
	flight flightGroup
	bucket *tokenBucket
	client *http.Client

	upRequests   atomic.Int64
	upErrors     atomic.Int64
	publicUsed   atomic.Int64
	publicDenied atomic.Int64
}

func NewGate(cfg Config) *Gate {
	return &Gate{
		cfg:    cfg,
		pool:   newKeyPool(cfg.PoolConfig, cfg.PoolState, cfg.Provider, cfg.ReloadEvery),
		cache:  newLRU(cfg.CacheSize, cfg.CacheTTL, cfg.CacheMaxEntry),
		bucket: newBucket(cfg.PublicRPS, cfg.PublicBurst),
		client: &http.Client{Timeout: cfg.UpstreamTimeout},
	}
}

func (g *Gate) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", g.handleHealth)
	mux.HandleFunc("/solana", g.handleRPC)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		g.handleRPC(w, r)
	})
	return g.auth(mux)
}

func (g *Gate) auth(next http.Handler) http.Handler {
	if g.cfg.Token == "" {
		return next
	}
	want := []byte(g.cfg.Token)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if got == "" {
			got = r.URL.Query().Get("token")
		}
		if subtle.ConstantTimeCompare([]byte(got), want) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

type rpcReq struct {
	ID     json.RawMessage `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

func (g *Gate) handleRPC(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "POST JSON-RPC only"})
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, g.cfg.MaxRequestBytes))
	if err != nil {
		writeRPCError(w, http.StatusRequestEntityTooLarge, nil, -32600, "request too large or unreadable")
		return
	}
	trim := bytes.TrimSpace(body)
	if len(trim) > 0 && trim[0] == '[' {
		g.serveBatch(w, r.Context(), body)
		return
	}
	var req rpcReq
	if err := json.Unmarshal(trim, &req); err != nil || req.Method == "" {
		writeRPCError(w, http.StatusOK, nil, -32700, "parse error")
		return
	}
	g.serveSingle(w, r.Context(), body, req)
}

func (g *Gate) serveSingle(w http.ResponseWriter, ctx context.Context, body []byte, req rpcReq) {
	ck, cacheable := "", false
	if g.cfg.CacheSize > 0 {
		ck, cacheable = cacheKeyFor(req.Method, req.Params)
	}
	if !cacheable {
		writeUpstream(w, g.forward(ctx, body, req.Method != "sendTransaction"))
		return
	}
	if res, ok := g.cache.get(ck); ok {
		writeRaw(w, http.StatusOK, buildResult(req.ID, res))
		return
	}
	// Detach from this caller's cancellation: followers share the result.
	fctx := context.WithoutCancel(ctx)
	res, leader := g.flight.do(ck, func() upstreamResult {
		res := g.forward(fctx, body, true)
		if res.status == http.StatusOK {
			var obj map[string]json.RawMessage
			if json.Unmarshal(res.body, &obj) == nil {
				if v, ok := storableResult(req.Method, obj, time.Now(), g.cfg.FinalityAge); ok {
					g.cache.put(ck, v)
				}
			}
		}
		return res
	})
	if !leader {
		res.body = reID(res.body, req.ID)
	}
	writeUpstream(w, res)
}

func (g *Gate) serveBatch(w http.ResponseWriter, ctx context.Context, body []byte) {
	var reqs []rpcReq
	if err := json.Unmarshal(body, &reqs); err != nil || len(reqs) == 0 {
		writeUpstream(w, g.forward(ctx, body, false))
		return
	}
	retryable := true
	keys := make([]string, len(reqs))
	allCacheable := g.cfg.CacheSize > 0
	for i, rq := range reqs {
		if rq.Method == "sendTransaction" {
			retryable = false
		}
		if g.cfg.CacheSize > 0 {
			if k, ok := cacheKeyFor(rq.Method, rq.Params); ok {
				keys[i] = k
				continue
			}
		}
		allCacheable = false
	}
	// Fully served from cache only if every element hits; otherwise the batch
	// is forwarded as-is so the upstream response stays verbatim.
	if allCacheable {
		out := make([]json.RawMessage, 0, len(reqs))
		for i, rq := range reqs {
			res, ok := g.cache.get(keys[i])
			if !ok {
				out = nil
				break
			}
			out = append(out, buildResult(rq.ID, res))
		}
		if out != nil {
			b, _ := json.Marshal(out)
			writeRaw(w, http.StatusOK, b)
			return
		}
	}
	res := g.forward(ctx, body, retryable)
	writeUpstream(w, res)
	if res.status != http.StatusOK {
		return
	}
	var replies []map[string]json.RawMessage
	if json.Unmarshal(res.body, &replies) != nil {
		return
	}
	byID := map[string]map[string]json.RawMessage{}
	dup := map[string]bool{}
	for _, rp := range replies {
		id := string(bytes.TrimSpace(rp["id"]))
		if _, seen := byID[id]; seen {
			dup[id] = true
		}
		byID[id] = rp
	}
	for i, rq := range reqs {
		id := string(bytes.TrimSpace(rq.ID))
		if keys[i] == "" || id == "" || id == "null" || dup[id] {
			continue
		}
		if rp, ok := byID[id]; ok {
			if v, ok := storableResult(rq.Method, rp, time.Now(), g.cfg.FinalityAge); ok {
				g.cache.put(keys[i], v)
			}
		}
	}
}

// forward sends body to Helius (round-robin over non-cooling keys, rotating on
// 429/quota) and then to the rate-limited public RPC when no key is usable.
// When retryable is false (sendTransaction) the body goes upstream exactly once.
func (g *Gate) forward(ctx context.Context, body []byte, retryable bool) upstreamResult {
	tried := map[string]bool{}
	var last *upstreamResult
	for {
		key, ok := g.pool.pick(tried)
		if !ok {
			break
		}
		tried[key] = true
		res, err := g.post(ctx, g.heliusURL(key), body)
		if err != nil {
			g.upErrors.Add(1)
			log.Printf("rpc-gate: helius key %s: %v", maskKey(key), err)
			if !retryable {
				return localError(http.StatusBadGateway, "upstream error")
			}
			continue
		}
		if res.status >= 300 {
			g.upErrors.Add(1)
		}
		if cool, secs := classifyCooldown(res.status, res.body); cool {
			if err := g.pool.recordCooldown(key, secs); err != nil {
				log.Printf("rpc-gate: cooldown write failed for %s: %v", maskKey(key), err)
			}
			log.Printf("rpc-gate: helius key %s -> HTTP %d, cooling %.0fs", maskKey(key), res.status, secs)
			if !retryable {
				return res
			}
			continue
		}
		if res.status >= 500 && retryable {
			r := res
			last = &r
			continue
		}
		return res
	}
	if !retryable && len(tried) > 0 {
		return localError(http.StatusBadGateway, "upstream error")
	}
	wctx, cancel := context.WithTimeout(ctx, g.cfg.PublicWait)
	err := g.bucket.wait(wctx)
	cancel()
	if err != nil {
		g.publicDenied.Add(1)
		if last != nil {
			return *last
		}
		return localError(http.StatusTooManyRequests, "rpc-gate: all keys cooling; "+err.Error())
	}
	g.publicUsed.Add(1)
	res, perr := g.post(ctx, g.cfg.PublicRPC, body)
	if perr != nil {
		g.upErrors.Add(1)
		log.Printf("rpc-gate: public rpc: %v", perr)
		return localError(http.StatusBadGateway, "public upstream error")
	}
	if res.status >= 300 {
		g.upErrors.Add(1)
	}
	return res
}

func (g *Gate) heliusURL(key string) string {
	u, err := url.Parse(g.cfg.HeliusBase)
	if err != nil {
		return g.cfg.HeliusBase
	}
	q := u.Query()
	q.Set("api-key", key)
	u.RawQuery = q.Encode()
	return u.String()
}

// post never returns an error that embeds the request URL (it carries the key).
func (g *Gate) post(ctx context.Context, target string, body []byte) (upstreamResult, error) {
	g.upRequests.Add(1)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, target, bytes.NewReader(body))
	if err != nil {
		return upstreamResult{}, errors.New("bad upstream url")
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := g.client.Do(req)
	if err != nil {
		var ue *url.Error
		if errors.As(err, &ue) {
			return upstreamResult{}, ue.Err
		}
		return upstreamResult{}, errors.New("upstream request failed")
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, g.cfg.MaxRespBytes))
	if err != nil {
		return upstreamResult{}, errors.New("upstream read failed")
	}
	return upstreamResult{status: resp.StatusCode, body: b, ctype: resp.Header.Get("Content-Type")}, nil
}

func (g *Gate) handleHealth(w http.ResponseWriter, r *http.Request) {
	total, avail, rows := g.pool.status()
	writeJSON(w, http.StatusOK, map[string]any{
		"service":                "ares-rpc-gate",
		"provider":               g.cfg.Provider,
		"keys":                   total,
		"available":              avail,
		"per_key":                rows,
		"public_fallback_used":   g.publicUsed.Load(),
		"public_fallback_denied": g.publicDenied.Load(),
		"public_rps":             g.cfg.PublicRPS,
		"cache": map[string]any{
			"hits": g.cache.hits.Load(), "misses": g.cache.misses.Load(),
			"size": g.cache.size(), "coalesced": g.flight.coalesced.Load(),
		},
		"upstream": map[string]any{"requests": g.upRequests.Load(), "errors": g.upErrors.Load()},
	})
}

// ---- response helpers ----------------------------------------------------

func buildResult(id, result json.RawMessage) json.RawMessage {
	if len(bytes.TrimSpace(id)) == 0 {
		id = json.RawMessage("null")
	}
	b, _ := json.Marshal(map[string]json.RawMessage{"jsonrpc": json.RawMessage(`"2.0"`), "result": result, "id": id})
	return b
}

// reID rewrites the top-level id of a single JSON-RPC response for a coalesced follower.
func reID(body []byte, id json.RawMessage) []byte {
	var obj map[string]json.RawMessage
	if json.Unmarshal(body, &obj) != nil {
		return body
	}
	if len(bytes.TrimSpace(id)) == 0 {
		id = json.RawMessage("null")
	}
	obj["id"] = id
	b, err := json.Marshal(obj)
	if err != nil {
		return body
	}
	return b
}

func localError(status int, msg string) upstreamResult {
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "error": map[string]any{"code": -32005, "message": msg}, "id": nil})
	return upstreamResult{status: status, body: b, ctype: "application/json"}
}

func writeUpstream(w http.ResponseWriter, res upstreamResult) {
	ct := res.ctype
	if ct == "" {
		ct = "application/json"
	}
	w.Header().Set("Content-Type", ct)
	w.WriteHeader(res.status)
	w.Write(res.body)
}

func writeRaw(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(b)
}

func writeRPCError(w http.ResponseWriter, status int, id json.RawMessage, code int, msg string) {
	if id == nil {
		id = json.RawMessage("null")
	}
	b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "error": map[string]any{"code": code, "message": msg}, "id": id})
	writeRaw(w, status, b)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	b, _ := json.Marshal(v)
	writeRaw(w, status, b)
}

func isLoopback(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func main() {
	cfg := configFromEnv()
	if !isLoopback(cfg.Addr) && cfg.Token == "" {
		log.Fatalf("rpc-gate: refusing non-loopback bind %q without RPC_GATE_TOKEN", cfg.Addr)
	}
	g := NewGate(cfg)
	total, avail, _ := g.pool.status()
	log.Printf("rpc-gate: listening on %s provider=%s keys=%d available=%d public_rps=%g cache=%d",
		cfg.Addr, cfg.Provider, total, avail, cfg.PublicRPS, cfg.CacheSize)
	srv := &http.Server{Addr: cfg.Addr, Handler: g.Handler(), ReadHeaderTimeout: 10 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
