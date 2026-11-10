package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Fake keys are short on purpose (repo secret scanner) and never real.
var testKeys = []string{"tk-alpha-0001", "tk-bravo-0002", "tk-charlie-0003"}

type fakeUpstream struct {
	srv    *httptest.Server
	mu     sync.Mutex
	hits   map[string]int // api-key -> count ("" for public)
	bodies []string
	total  atomic.Int64
	// behaviour(key, body) -> status, body; nil = default ok
	behave func(key string, body []byte) (int, string)
}

func newFakeUpstream(t *testing.T) *fakeUpstream {
	f := &fakeUpstream{hits: map[string]int{}}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		k := r.URL.Query().Get("api-key")
		f.mu.Lock()
		f.hits[k]++
		f.bodies = append(f.bodies, string(b))
		behave := f.behave
		f.mu.Unlock()
		f.total.Add(1)
		status, out := 200, ""
		if behave != nil {
			status, out = behave(k, b)
		}
		if out == "" {
			out = defaultReply(b, k)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		io.WriteString(w, out)
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeUpstream) hitsFor(k string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.hits[k]
}

// defaultReply echoes ids back with a result tagged by which upstream served it.
func defaultReply(body []byte, key string) string {
	tag := "public"
	if key != "" {
		tag = "helius"
	}
	one := func(r rpcReq) string {
		id := string(r.ID)
		if id == "" {
			id = "null"
		}
		res := fmt.Sprintf(`{"served_by":%q,"method":%q}`, tag, r.Method)
		if r.Method == "getSlot" {
			res = "123456"
		}
		return fmt.Sprintf(`{"jsonrpc":"2.0","result":%s,"id":%s}`, res, id)
	}
	tb := bytes.TrimSpace(body)
	if len(tb) > 0 && tb[0] == '[' {
		var rs []rpcReq
		json.Unmarshal(tb, &rs)
		parts := make([]string, len(rs))
		for i, r := range rs {
			parts[i] = one(r)
		}
		return "[" + strings.Join(parts, ",") + "]"
	}
	var r rpcReq
	json.Unmarshal(tb, &r)
	return one(r)
}

type harness struct {
	gate      *Gate
	srv       *httptest.Server
	helius    *fakeUpstream
	public    *fakeUpstream
	statePath string
	cfgPath   string
}

func newHarness(t *testing.T, keys []string, mutate func(*Config)) *harness {
	t.Helper()
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "pools.json")
	statePath := filepath.Join(dir, "state.json")
	cfgJSON, _ := json.Marshal(map[string]any{
		"helius":  map[string]any{"keys": keys, "assignments": map[string]int{"early_buyer": 0}},
		"birdeye": map[string]any{"keys": []string{"bd-0001"}, "assignments": map[string]int{}},
	})
	if err := os.WriteFile(cfgPath, cfgJSON, 0o600); err != nil {
		t.Fatal(err)
	}
	h := &harness{helius: newFakeUpstream(t), public: newFakeUpstream(t), statePath: statePath, cfgPath: cfgPath}
	cfg := Config{
		Addr: "127.0.0.1:0", PoolConfig: cfgPath, PoolState: statePath, Provider: "helius",
		HeliusBase: h.helius.srv.URL + "/", PublicRPC: h.public.srv.URL,
		PublicRPS: 1000, PublicBurst: 1000, PublicWait: time.Second,
		CacheSize: 100, CacheTTL: time.Minute, CacheMaxEntry: 1 << 20,
		UpstreamTimeout: 5 * time.Second, ReloadEvery: 0, FinalityAge: 2 * time.Minute,
		MaxRequestBytes: 1 << 20, MaxRespBytes: 8 << 20,
	}
	if mutate != nil {
		mutate(&cfg)
	}
	h.gate = NewGate(cfg)
	h.srv = httptest.NewServer(h.gate.Handler())
	t.Cleanup(h.srv.Close)
	return h
}

func (h *harness) rpc(t *testing.T, body string) (int, string) {
	t.Helper()
	resp, err := http.Post(h.srv.URL+"/", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(b)
}

func (h *harness) health(t *testing.T) (map[string]any, string) {
	t.Helper()
	resp, err := http.Get(h.srv.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("health not json: %s", b)
	}
	return m, string(b)
}

func readState(t *testing.T, p string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("state not flat json: %s", b)
	}
	return m
}

const quotaBody = `{"jsonrpc":"2.0","error":{"code":-32429,"message":"max usage reached"},"id":1}`

func slotReq(id int) string { return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"getSlot"}`, id) }

func TestRoundRobinDistribution(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	for i := 0; i < 30; i++ {
		if st, _ := h.rpc(t, slotReq(i)); st != 200 {
			t.Fatalf("status %d", st)
		}
	}
	for _, k := range testKeys {
		if got := h.helius.hitsFor(k); got != 10 {
			t.Errorf("key %s got %d requests, want 10", maskKey(k), got)
		}
	}
	if h.public.total.Load() != 0 {
		t.Errorf("public used while keys available")
	}
}

func TestQuota429WritesPythonFormatCooldownAndSkipsKey(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	// Pre-existing entries written "by Python" (other provider, int value) must survive.
	os.WriteFile(h.statePath, []byte("{\n  \"birdeye:bd-0001\": 1700000000,\n  \"helius:other\": 1.5\n}"), 0o600)
	capped := testKeys[1]
	h.helius.behave = func(k string, b []byte) (int, string) {
		if k == capped {
			return 429, quotaBody
		}
		return 200, ""
	}
	before := float64(time.Now().Unix())
	for i := 0; i < 9; i++ {
		st, body := h.rpc(t, slotReq(i))
		if st != 200 || !strings.Contains(body, "123456") {
			t.Fatalf("req %d: %d %s", i, st, body)
		}
	}
	if got := h.helius.hitsFor(capped); got != 1 {
		t.Errorf("capped key hit %d times, want exactly 1 then skipped", got)
	}
	st := readState(t, h.statePath)
	until, ok := st["helius:"+capped].(float64)
	if !ok {
		t.Fatalf("no float cooldown for capped key in state: %v", st)
	}
	if d := until - before; d < CooldownHardQuota-5 || d > CooldownHardQuota+5 {
		t.Errorf("hard-quota cooldown = %.1fs, want ~%v", d, CooldownHardQuota)
	}
	if st["birdeye:bd-0001"] != float64(1700000000) || st["helius:other"] != 1.5 {
		t.Errorf("pre-existing entries not preserved: %v", st)
	}
	fi, _ := os.Stat(h.statePath)
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("state perm %v, want 0600", fi.Mode().Perm())
	}
	if _, err := os.Stat(h.statePath + ".tmp"); err == nil {
		t.Errorf("tmp file left behind")
	}
}

func TestPlain429ShortCooldown(t *testing.T) {
	h := newHarness(t, testKeys[:2], nil)
	h.helius.behave = func(k string, b []byte) (int, string) {
		if k == testKeys[0] {
			return 429, `{"error":"Too Many Requests"}`
		}
		return 200, ""
	}
	before := float64(time.Now().Unix())
	h.rpc(t, slotReq(1))
	until := readState(t, h.statePath)["helius:"+testKeys[0]].(float64)
	if d := until - before; d < CooldownRateLimit-5 || d > CooldownRateLimit+5 {
		t.Errorf("rate-limit cooldown = %.1fs, want ~%v", d, CooldownRateLimit)
	}
}

func TestClientErrorDoesNotCool(t *testing.T) {
	h := newHarness(t, testKeys[:1], nil)
	h.helius.behave = func(k string, b []byte) (int, string) { return 400, `{"error":"bad params"}` }
	h.rpc(t, slotReq(1))
	if _, err := os.Stat(h.statePath); err == nil {
		t.Errorf("plain 400 without quota phrase must not write a cooldown")
	}
}

func TestHonoursExternalCooldownAndReloads(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	// Written by "Python" after the gate started: key 0 cooling for an hour.
	future := float64(time.Now().Unix() + 3600)
	os.WriteFile(h.statePath, []byte(fmt.Sprintf(`{"helius:%s": %f}`, testKeys[0], future)), 0o600)
	for i := 0; i < 10; i++ {
		h.rpc(t, slotReq(i))
	}
	if got := h.helius.hitsFor(testKeys[0]); got != 0 {
		t.Errorf("externally cooled key used %d times", got)
	}
	// Expired cooldown -> key back in rotation.
	os.WriteFile(h.statePath, []byte(fmt.Sprintf(`{"helius:%s": 1.0}`, testKeys[0])), 0o600)
	for i := 0; i < 6; i++ {
		h.rpc(t, slotReq(i))
	}
	if got := h.helius.hitsFor(testKeys[0]); got == 0 {
		t.Errorf("expired cooldown key never re-used")
	}
}

func TestAllCappedFallsBackToPublic(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	h.helius.behave = func(k string, b []byte) (int, string) { return 429, quotaBody }
	st, body := h.rpc(t, `{"jsonrpc":"2.0","id":7,"method":"getBalance","params":["Addr1"]}`)
	if st != 200 || !strings.Contains(body, `"served_by":"public"`) || !strings.Contains(body, `"id":7`) {
		t.Fatalf("expected public verbatim reply, got %d %s", st, body)
	}
	if h.helius.total.Load() != 3 {
		t.Errorf("helius attempts %d, want 3 (one per key)", h.helius.total.Load())
	}
	// Second request: every key cooling -> straight to public, no helius traffic.
	h.rpc(t, slotReq(8))
	if h.helius.total.Load() != 3 {
		t.Errorf("cooling keys were retried")
	}
	m, _ := h.health(t)
	if m["public_fallback_used"].(float64) != 2 || m["available"].(float64) != 0 {
		t.Errorf("health: %v", m)
	}
}

func TestPublicFallbackIsRateLimited(t *testing.T) {
	h := newHarness(t, nil, func(c *Config) { c.PublicRPS = 2; c.PublicBurst = 2; c.PublicWait = 50 * time.Millisecond })
	codes := map[int]int{}
	for i := 0; i < 6; i++ {
		st, _ := h.rpc(t, slotReq(i))
		codes[st]++
	}
	if got := h.public.total.Load(); got != 2 {
		t.Errorf("public upstream got %d requests in a burst, want 2 (bucket)", got)
	}
	if codes[429] != 4 {
		t.Errorf("want 4 local 429s, got %v", codes)
	}
}

func TestCacheHitGetTransaction(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	req := func(id int, extra string) string {
		return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"getTransaction","params":["SigAAA"%s]}`, id, extra)
	}
	_, b1 := h.rpc(t, req(1, `,{"encoding":"json","maxSupportedTransactionVersion":0}`))
	// same params, different key order + id -> cache hit
	_, b2 := h.rpc(t, req(2, `,{"maxSupportedTransactionVersion":0,"encoding":"json"}`))
	if h.helius.total.Load() != 1 {
		t.Fatalf("upstream calls %d, want 1 (second is a cache hit)", h.helius.total.Load())
	}
	if !strings.Contains(b1, `"id":1`) || !strings.Contains(b2, `"id":2`) || !strings.Contains(b2, "getTransaction") {
		t.Errorf("bad bodies: %s | %s", b1, b2)
	}
	// confirmed commitment -> not cacheable
	h.rpc(t, req(3, `,{"commitment":"confirmed"}`))
	h.rpc(t, req(4, `,{"commitment":"confirmed"}`))
	if h.helius.total.Load() != 3 {
		t.Errorf("confirmed getTransaction was cached")
	}
	// null result (not found yet) -> never cached
	h.helius.behave = func(k string, b []byte) (int, string) { return 200, `{"jsonrpc":"2.0","result":null,"id":5}` }
	h.rpc(t, req(5, `,{"encoding":"base64"}`))
	h.rpc(t, req(5, `,{"encoding":"base64"}`))
	if h.helius.total.Load() != 5 {
		t.Errorf("null getTransaction result was cached")
	}
	m, _ := h.health(t)
	c := m["cache"].(map[string]any)
	if c["hits"].(float64) != 1 || c["size"].(float64) != 1 {
		t.Errorf("cache stats %v", c)
	}
}

func TestNoCacheForMutableMethods(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	for _, r := range []string{
		slotReq(1), slotReq(1),
		`{"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash"}`, `{"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash"}`,
		`{"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":["X"]}`, `{"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":["X"]}`,
		// newest signatures page (no `before`) is mutable
		`{"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":["X",{"limit":10}]}`,
		`{"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":["X",{"limit":10}]}`,
	} {
		h.rpc(t, r)
	}
	if h.helius.total.Load() != 8 {
		t.Errorf("upstream %d, want 8 (nothing cached)", h.helius.total.Load())
	}
}

func TestCacheSignaturesBeforeAndBlockTime(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	old := time.Now().Add(-time.Hour).Unix()
	recent := time.Now().Unix()
	h.helius.behave = func(k string, b []byte) (int, string) {
		var r rpcReq
		json.Unmarshal(b, &r)
		switch {
		case r.Method == "getSignaturesForAddress":
			return 200, `{"jsonrpc":"2.0","result":[{"signature":"s1","confirmationStatus":"finalized"}],"id":1}`
		case r.Method == "getBlockTime" && strings.Contains(string(r.Params), "100"):
			return 200, fmt.Sprintf(`{"jsonrpc":"2.0","result":%d,"id":1}`, old)
		case r.Method == "getBlockTime":
			return 200, fmt.Sprintf(`{"jsonrpc":"2.0","result":%d,"id":1}`, recent)
		}
		return 200, ""
	}
	sig := `{"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":["X",{"before":"sigZ","limit":10}]}`
	h.rpc(t, sig)
	h.rpc(t, sig)
	if h.helius.total.Load() != 1 {
		t.Errorf("before-page not cached: %d", h.helius.total.Load())
	}
	h.rpc(t, `{"jsonrpc":"2.0","id":1,"method":"getBlockTime","params":[100]}`)
	h.rpc(t, `{"jsonrpc":"2.0","id":1,"method":"getBlockTime","params":[100]}`)
	if h.helius.total.Load() != 2 {
		t.Errorf("old block time not cached: %d", h.helius.total.Load())
	}
	h.rpc(t, `{"jsonrpc":"2.0","id":1,"method":"getBlockTime","params":[999]}`)
	h.rpc(t, `{"jsonrpc":"2.0","id":1,"method":"getBlockTime","params":[999]}`)
	if h.helius.total.Load() != 4 {
		t.Errorf("recent (maybe-unfinalized) block time was cached: %d", h.helius.total.Load())
	}
}

func TestCoalescesConcurrentIdentical(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	release := make(chan struct{})
	h.helius.behave = func(k string, b []byte) (int, string) {
		<-release
		return 200, ""
	}
	const N = 25
	var wg sync.WaitGroup
	bodies := make([]string, N)
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, bodies[i] = h.rpc(t, fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"getBlock","params":[4242,{"transactionDetails":"none"}]}`, 1000+i))
		}(i)
	}
	// let every request reach the gateway, then release the single upstream call
	deadline := time.Now().Add(3 * time.Second)
	for h.gate.flight.coalesced.Load() < N-1 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	close(release)
	wg.Wait()
	if got := h.helius.total.Load(); got != 1 {
		t.Fatalf("upstream calls %d, want 1", got)
	}
	if c := h.gate.flight.coalesced.Load(); c != N-1 {
		t.Errorf("coalesced %d, want %d", c, N-1)
	}
	for i, b := range bodies {
		if !strings.Contains(b, fmt.Sprintf(`"id":%d`, 1000+i)) || !strings.Contains(b, "getBlock") {
			t.Errorf("caller %d got wrong body: %s", i, b)
		}
	}
}

func TestBatchRequests(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	batch := `[{"jsonrpc":"2.0","id":1,"method":"getSlot"},{"jsonrpc":"2.0","id":2,"method":"getTransaction","params":["SigB"]}]`
	st, body := h.rpc(t, batch)
	var arr []map[string]any
	if st != 200 || json.Unmarshal([]byte(body), &arr) != nil || len(arr) != 2 {
		t.Fatalf("batch reply %d %s", st, body)
	}
	if h.helius.total.Load() != 1 || h.helius.bodies[0] != batch {
		t.Errorf("batch not forwarded verbatim as one call")
	}
	// all-cacheable batch fully served from the cache populated above
	st, body = h.rpc(t, `[{"jsonrpc":"2.0","id":9,"method":"getTransaction","params":["SigB"]}]`)
	if st != 200 || h.helius.total.Load() != 1 || !strings.Contains(body, `"id":9`) {
		t.Errorf("batch cache hit failed: %d %s upstream=%d", st, body, h.helius.total.Load())
	}
	// batch rotates past a capped key like single requests do
	h.helius.behave = func(k string, b []byte) (int, string) {
		if k == testKeys[0] || k == testKeys[1] {
			return 429, quotaBody
		}
		return 200, ""
	}
	h2 := h
	for i := 0; i < 3; i++ {
		if st, body := h2.rpc(t, batch); st != 200 || !strings.HasPrefix(body, "[") {
			t.Fatalf("batch after cap: %d %s", st, body)
		}
	}
}

func TestSendTransactionForwardedOnce(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	h.helius.behave = func(k string, b []byte) (int, string) { return 429, quotaBody }
	st, body := h.rpc(t, `{"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":["AQID"]}`)
	if st != 429 || body != quotaBody {
		t.Errorf("want verbatim 429, got %d %s", st, body)
	}
	if h.helius.total.Load() != 1 || h.public.total.Load() != 0 {
		t.Errorf("sendTransaction retried: helius=%d public=%d", h.helius.total.Load(), h.public.total.Load())
	}
}

func TestHealthMaskedAndTokenAuth(t *testing.T) {
	h := newHarness(t, testKeys, func(c *Config) { c.Token = "tok" + "-test" })
	resp, _ := http.Get(h.srv.URL + "/health")
	if resp.StatusCode != 401 {
		t.Fatalf("no-token health status %d", resp.StatusCode)
	}
	resp.Body.Close()
	req, _ := http.NewRequest("GET", h.srv.URL+"/health", nil)
	req.Header.Set("Authorization", "Bearer tok-test")
	resp, err := http.DefaultClient.Do(req)
	if err != nil || resp.StatusCode != 200 {
		t.Fatalf("bearer health failed: %v", err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	for _, k := range testKeys {
		if strings.Contains(string(b), k) || strings.Contains(string(b), k[:len(k)-4]) {
			t.Errorf("health leaks key material: %s", b)
		}
	}
	var m map[string]any
	json.Unmarshal(b, &m)
	pk := m["per_key"].([]any)
	if len(pk) != 3 || pk[0].(map[string]any)["key"] != "…0001" || m["keys"].(float64) != 3 {
		t.Errorf("per_key: %v", m)
	}
	for _, f := range []string{"available", "public_fallback_used", "cache", "upstream"} {
		if _, ok := m[f]; !ok {
			t.Errorf("health missing %s", f)
		}
	}
	// ?token= works for URL-only clients (early_buyer passes a bare URL)
	resp, _ = http.Post(h.srv.URL+"/solana?token=tok-test", "application/json", strings.NewReader(slotReq(1)))
	if resp.StatusCode != 200 {
		t.Errorf("query-token rpc status %d", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestUpstreamErrorsNeverLeakKey(t *testing.T) {
	h := newHarness(t, testKeys[:1], func(c *Config) { c.HeliusBase = "http://127.0.0.1:1/"; c.PublicRPC = "http://127.0.0.1:1/" })
	_, body := h.rpc(t, `{"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":["AQID"]}`)
	if strings.Contains(body, testKeys[0]) {
		t.Errorf("error body leaks key: %s", body)
	}
	_, body = h.rpc(t, slotReq(2))
	if strings.Contains(body, testKeys[0]) {
		t.Errorf("error body leaks key: %s", body)
	}
}

func TestSolanaPathAndMethodGuard(t *testing.T) {
	h := newHarness(t, testKeys, nil)
	resp, _ := http.Post(h.srv.URL+"/solana", "application/json", strings.NewReader(slotReq(1)))
	if resp.StatusCode != 200 {
		t.Errorf("/solana status %d", resp.StatusCode)
	}
	resp.Body.Close()
	resp, _ = http.Get(h.srv.URL + "/")
	if resp.StatusCode != 405 {
		t.Errorf("GET / status %d", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestLoopbackGuard(t *testing.T) {
	for addr, want := range map[string]bool{"127.0.0.1:8092": true, "localhost:8092": true, "[::1]:8092": true, "0.0.0.0:8092": false, ":8092": false} {
		if isLoopback(addr) != want {
			t.Errorf("isLoopback(%q) != %v", addr, want)
		}
	}
}
