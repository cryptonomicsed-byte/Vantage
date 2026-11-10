// Key pool -- Go side of /opt/ares/api_key_pool.py.
//
// Reads the SAME two files the Python pool uses and writes cooldowns in the
// identical format, so the Python daemons and this gateway agree on which
// Helius keys are cooling down:
//
//	config  /opt/ares/.api_key_pools.json  {"helius": {"keys": [...], "assignments": {...}}, ...}
//	state   /opt/ares/.api_key_pool_state.json  flat {"helius:<key>": <cooldown_until_epoch_float>, ...}
//
// Semantics mirrored from api_key_pool.report_error:
//   - only status 429/400/402/403 can cool a key;
//   - body containing a QUOTA_PHRASE -> COOLDOWN_HARD_QUOTA (3600s),
//     otherwise COOLDOWN_RATE_LIMIT (120s).
//
// One deliberate narrowing: Python cools on a plain 400/402/403 too (its
// callers pre-filter on "usage" in the body). A proxy sees arbitrary client
// errors, so here a 400/402/403 only cools a key when a quota phrase is in
// the body; 429 always cools.
//
// Writes use the Python pattern: <state>.tmp + flock(LOCK_EX) + os.replace +
// chmod 0600, and are read-modify-write against the file on disk, so entries
// written by Python (other providers, other keys) are preserved verbatim.
//
// Config and state are re-read only when their mtime/size changes (checked
// at most every reloadEvery), never parsed per request.
package main

import (
	"encoding/json"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// Mirrors api_key_pool.py -- keep in sync.
const (
	CooldownHardQuota = 3600.0 // COOLDOWN_HARD_QUOTA
	CooldownRateLimit = 120.0  // COOLDOWN_RATE_LIMIT
)

var QuotaPhrases = []string{"usage limit exceeded", "max usage reached", "compute units"}

type fileSig struct {
	mod  time.Time
	size int64
	ok   bool
}

func statSig(path string) fileSig {
	fi, err := os.Stat(path)
	if err != nil {
		return fileSig{}
	}
	return fileSig{mod: fi.ModTime(), size: fi.Size(), ok: true}
}

type keyPool struct {
	cfgPath, statePath, provider string
	reloadEvery                  time.Duration
	now                          func() time.Time

	mu        sync.Mutex
	keys      []string
	state     map[string]float64
	cfgSig    fileSig
	stateSig  fileSig
	lastCheck time.Time
	loaded    bool

	rr atomic.Uint64
}

func newKeyPool(cfgPath, statePath, provider string, reloadEvery time.Duration) *keyPool {
	p := &keyPool{cfgPath: cfgPath, statePath: statePath, provider: provider,
		reloadEvery: reloadEvery, now: time.Now, state: map[string]float64{}}
	p.mu.Lock()
	p.refreshLocked(true)
	p.mu.Unlock()
	return p
}

func epoch(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

// refreshLocked re-reads config/state if their signature changed.
func (p *keyPool) refreshLocked(force bool) {
	now := p.now()
	if !force && p.loaded && now.Sub(p.lastCheck) < p.reloadEvery {
		return
	}
	p.lastCheck = now
	p.loaded = true
	if sig := statSig(p.cfgPath); force || sig != p.cfgSig {
		p.cfgSig = sig
		if keys, err := loadProviderKeys(p.cfgPath, p.provider); err == nil {
			p.keys = keys
		} else if !os.IsNotExist(err) {
			log.Printf("rpc-gate: config reload failed (keeping %d keys): %v", len(p.keys), err)
		} else {
			p.keys = nil
		}
	}
	if sig := statSig(p.statePath); force || sig != p.stateSig {
		p.stateSig = sig
		raw, err := readRawState(p.statePath)
		if err == nil {
			p.state = numericState(raw)
		} else if os.IsNotExist(err) {
			p.state = map[string]float64{}
		} else {
			log.Printf("rpc-gate: state reload failed (keeping previous): %v", err)
		}
	}
}

func loadProviderKeys(path, provider string) ([]string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(b, &top); err != nil {
		return nil, err
	}
	var prov struct {
		Keys []string `json:"keys"`
	}
	if raw, ok := top[provider]; ok {
		if err := json.Unmarshal(raw, &prov); err != nil {
			return nil, err
		}
	}
	out := make([]string, 0, len(prov.Keys))
	for _, k := range prov.Keys {
		if k = strings.TrimSpace(k); k != "" {
			out = append(out, k)
		}
	}
	return out, nil
}

// readRawState loads the flat state file. A missing file is os.ErrNotExist;
// an empty/invalid file reads as {} (same as Python's _load_json default).
func readRawState(path string) (map[string]json.RawMessage, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	raw := map[string]json.RawMessage{}
	if len(strings.TrimSpace(string(b))) == 0 {
		return raw, nil
	}
	if err := json.Unmarshal(b, &raw); err != nil {
		return map[string]json.RawMessage{}, nil
	}
	return raw, nil
}

func numericState(raw map[string]json.RawMessage) map[string]float64 {
	out := make(map[string]float64, len(raw))
	for k, v := range raw {
		if f, err := strconv.ParseFloat(strings.TrimSpace(string(v)), 64); err == nil {
			out[k] = f
		}
	}
	return out
}

func (p *keyPool) stateKey(k string) string { return p.provider + ":" + k }

func (p *keyPool) coolingLocked(k string, now float64) bool {
	return p.state[p.stateKey(k)] > now
}

// pick returns the next round-robin key that is not cooling and not in tried.
func (p *keyPool) pick(tried map[string]bool) (string, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.refreshLocked(false)
	n := len(p.keys)
	if n == 0 {
		return "", false
	}
	now := epoch(p.now())
	start := int(p.rr.Add(1)-1) % n
	for i := 0; i < n; i++ {
		k := p.keys[(start+i)%n]
		if tried[k] || p.coolingLocked(k, now) {
			continue
		}
		return k, true
	}
	return "", false
}

// classifyCooldown reports whether an upstream reply should cool the key and
// for how long (seconds), mirroring report_error.
func classifyCooldown(status int, body []byte) (bool, float64) {
	switch status {
	case 429, 400, 402, 403:
	default:
		return false, 0
	}
	hard := hasQuotaPhrase(body)
	if status != 429 && !hard {
		return false, 0
	}
	if hard {
		return true, CooldownHardQuota
	}
	return true, CooldownRateLimit
}

func hasQuotaPhrase(body []byte) bool {
	if len(body) > 8192 {
		body = body[:8192]
	}
	l := strings.ToLower(string(body))
	for _, ph := range QuotaPhrases {
		if strings.Contains(l, ph) {
			return true
		}
	}
	return false
}

// recordCooldown writes "<provider>:<key>": now+seconds into the shared state
// file, merging with whatever is on disk right now.
func (p *keyPool) recordCooldown(key string, seconds float64) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	raw, err := readRawState(p.statePath)
	if err != nil {
		raw = map[string]json.RawMessage{}
	}
	until := epoch(p.now()) + seconds
	raw[p.stateKey(key)] = json.RawMessage(strconv.FormatFloat(until, 'f', -1, 64))
	data, err := json.MarshalIndent(raw, "", "  ")
	if err != nil {
		return err
	}
	if err := saveJSONAtomic(p.statePath, data); err != nil {
		return err
	}
	p.state = numericState(raw)
	p.stateSig = statSig(p.statePath)
	return nil
}

// saveJSONAtomic mirrors api_key_pool._save_json_atomic: write <path>.tmp under
// flock(LOCK_EX), os.replace onto path, chmod 0600. (Opens without O_TRUNC and
// truncates after taking the lock, which is the same protocol but does not
// clobber a concurrent writer's bytes before the lock is held.)
func saveJSONAtomic(path string, data []byte) error {
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE, 0o600)
	if err != nil {
		return err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		f.Close()
		return err
	}
	werr := f.Truncate(0)
	if werr == nil {
		_, werr = f.WriteAt(data, 0)
	}
	if werr == nil {
		werr = f.Sync()
	}
	syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	if cerr := f.Close(); werr == nil {
		werr = cerr
	}
	if werr != nil {
		return werr
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	_ = os.Chmod(path, 0o600)
	return nil
}

type keyStatus struct {
	Key                string `json:"key"`
	Cooling            bool   `json:"cooling"`
	CooldownRemainingS int64  `json:"cooldown_remaining_s"`
}

func (p *keyPool) status() (total, available int, rows []keyStatus) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.refreshLocked(false)
	now := epoch(p.now())
	rows = make([]keyStatus, 0, len(p.keys))
	for _, k := range p.keys {
		until := p.state[p.stateKey(k)]
		cooling := until > now
		rem := int64(0)
		if cooling {
			rem = int64(until - now + 0.5)
		} else {
			available++
		}
		rows = append(rows, keyStatus{Key: maskKey(k), Cooling: cooling, CooldownRemainingS: rem})
	}
	return len(p.keys), available, rows
}

// maskKey never returns more than the last 4 characters of a key.
func maskKey(k string) string {
	if len(k) <= 4 {
		return "…"
	}
	return "…" + k[len(k)-4:]
}
