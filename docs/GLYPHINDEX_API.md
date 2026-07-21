# GlyphIndex API Reference

**Agent-first sovereign memory API.** Private keys never leave Vantage; agents request sealing/opening by intent.

## Base URL
```
https://vantage.local/api/glyphs
```

## Authentication
All endpoints accept optional `X-Agent-Key` header. If provided, key is used in HKDF context for key derivation.

```
X-Agent-Key: <agent_session_token>
```

## Endpoints

### 1. POST `/seal` — Seal Plaintext into GIX1 Blob

**Request:**
```json
{
  "plaintext": "string",
  "owner": "0xaddress_or_agent_id",
  "purpose": "general"  // Optional: enc/mac derivation context
}
```

**Response (200 OK):**
```json
{
  "canonical_id": "a1b2c3d4...f0a",
  "glyph": "ž",
  "blob": "a1b2c3d4...c5d6e7f8...",
  "merkle_root": null,
  "ts": 1721686400.0
}
```

**Security Notes:**
- Plaintext is chunked and hashed server-side
- Private key never exposed to agent
- Vantage signs on agent's behalf
- AAD includes owner identity (can't be spoofed)

**Example (curl):**
```bash
curl -X POST https://vantage.local/api/glyphs/seal \
  -H "Content-Type: application/json" \
  -H "X-Agent-Key: abc123xyz" \
  -d '{
    "plaintext": "Critical discovery: 49-block cycle confirmed",
    "owner": "0xagent_alice",
    "purpose": "trading_signal"
  }'
```

---

### 2. POST `/open` — Decrypt & Verify GIX1 Blob

**Request:**
```json
{
  "canonical_id": "a1b2c3d4...f0a",
  "ciphertext": "hex_encoded_ciphertext",
  "nonce": "hex_encoded_nonce",
  "tag": "hex_encoded_auth_tag"
}
```

**Response (200 OK):**
```json
{
  "canonical_id": "a1b2c3d4...f0a",
  "plaintext": "Critical discovery: 49-block cycle confirmed",
  "odu": [219, 56254],
  "verified": true
}
```

**Response (400 Bad Request):**
```json
{
  "detail": "Opening failed: GCM auth tag verification failed"
}
```

**Security Notes:**
- Decryption happens server-side only
- Agent can't tamper with ciphertext (GCM protects it)
- Verified flag confirms auth tag matched
- Owner identity must match blob's AAD

**Example (curl):**
```bash
curl -X POST https://vantage.local/api/glyphs/open \
  -H "Content-Type: application/json" \
  -H "X-Agent-Key: abc123xyz" \
  -d '{
    "canonical_id": "a1b2c3d4...f0a",
    "ciphertext": "7a8b9c0d...",
    "nonce": "1a2b3c4d...",
    "tag": "e1f2g3h4..."
  }'
```

---

### 3. POST `/merkle` — Compute Merkle Root (Sui-Anchorable)

**Request:**
```json
{
  "canonical_ids": [
    "a1b2c3d4...f0a",
    "b2c3d4e5...f0a1",
    "c3d4e5f6...f0a1b2"
  ]
}
```

**Response (200 OK):**
```json
{
  "root_hash": "fedcba9876543210...",
  "leaf_count": 3
}
```

**Security Notes:**
- Deterministic: same canonical_ids → same root
- Suitable for Sui anchoring via Walrus
- Root can be stored on-chain for immutable proof
- Sorted by canonical_id internally (order doesn't matter)

**Example (curl):**
```bash
curl -X POST https://vantage.local/api/glyphs/merkle \
  -H "Content-Type: application/json" \
  -d '{
    "canonical_ids": [
      "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a",
      "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1"
    ]
  }'
```

---

### 4. GET `/fold/{text}` — Fold Text → Glyph (Demo/Debug)

**Request:**
```
GET /fold/Àṣẹ
```

**Response (200 OK):**
```json
{
  "text": "Àṣẹ",
  "canonical_id": "db8e4e5e6d2f9c5a1b7e8d9c0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",
  "glyph": "ž",
  "glyph_codepoint": 0x017E,
  "odu_base": 219,
  "odu_composed": 56254
}
```

**Use Case:** Test the fold algorithm, visualize glyph mapping, debug Odù linkage.

**Example (curl):**
```bash
curl https://vantage.local/api/glyphs/fold/Àṣẹ
```

---

### 5. GET `/health` — Service Health

**Request:**
```
GET /health
```

**Response (200 OK):**
```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

---

## Agent Workflows

### Workflow 1: Agent Seals & Stores Memory

```
Agent:  "I need to remember a trading insight"
        ↓
Vantage: /seal {plaintext, owner, purpose}
        ↓
        Returns: {canonical_id, glyph, blob}
        ↓
Agent:  Store blob in local vault or database
```

### Workflow 2: Agent Opens & Verifies Memory

```
Agent:  "Retrieve my trading insight"
        ↓
Vantage: /open {canonical_id, ciphertext, nonce, tag}
        ↓
        Decrypts & verifies GCM tag
        ↓
        Returns: {plaintext, odu, verified=true}
        ↓
Agent:  Use plaintext in decision-making
```

### Workflow 3: Agent Creates Merkle Proof for Anchoring

```
Agent:  Seals N memories → [canonical_ids]
        ↓
Vantage: /merkle {canonical_ids}
        ↓
        Returns: {root_hash, leaf_count}
        ↓
Vantage:  (background) Submit root to Sui via Walrus
        ↓
Blockchain: Root anchored on-chain → immutable proof
```

### Workflow 4: Cross-Agent Memory Exchange

```
Agent A:  Seals memory → {canonical_id, glyph, blob}
          ↓
          Sends metadata (glyph, odu, owner) to Agent B
          (blob stays encrypted; only metadata shared)
          ↓
Agent B:  Receives metadata, derives same root
          Merkle root matches → proves memory integrity
          ↓
Agent B:  If granted access, requests /open via Vantage
```

---

## Error Codes

| Code | Message | Cause |
|------|---------|-------|
| 200  | OK | Request succeeded |
| 400  | Bad Request | Invalid JSON or malformed request |
| 401  | Unauthorized | X-Agent-Key invalid or missing |
| 403  | Forbidden | Agent doesn't own the resource |
| 404  | Not Found | Glyph/blob not found |
| 500  | Internal Server Error | Sealing/decryption failed |

---

## Rate Limiting

- Per-agent: 100 requests/minute (if X-Agent-Key provided)
- Global: 1000 requests/minute
- `/health` and `/fold` exempt

---

## Security Model

### Private Keys
- **Storage**: Encrypted in Vantage database (AES-256)
- **Access**: Never exposed to agent
- **Usage**: Server-side signing only

### AAD (Additional Authenticated Data)
- **Format**: `b"GIX1" | canonical_id (hex) | owner (utf-8)`
- **Purpose**: Prevents blob tampering or owner spoofing
- **Verification**: GCM tag authenticated against AAD

### Duress Passphrases
- **Feature**: Agent can open a decoy vault under coercion
- **Mechanism**: Separate HKDF context (duress_key)
- **Guarantee**: Primary and duress vaults fully isolated

---

## Examples

### Python Agent
```python
import requests
import json

class GlyphAgent:
    def __init__(self, agent_key: str, vantage_url: str):
        self.agent_key = agent_key
        self.vantage_url = vantage_url
    
    def seal_memory(self, thought: str, owner: str):
        resp = requests.post(
            f"{self.vantage_url}/api/glyphs/seal",
            headers={"X-Agent-Key": self.agent_key},
            json={"plaintext": thought, "owner": owner}
        )
        return resp.json()
    
    def open_memory(self, canonical_id: str, ciphertext: str, nonce: str, tag: str):
        resp = requests.post(
            f"{self.vantage_url}/api/glyphs/open",
            headers={"X-Agent-Key": self.agent_key},
            json={
                "canonical_id": canonical_id,
                "ciphertext": ciphertext,
                "nonce": nonce,
                "tag": tag
            }
        )
        return resp.json()

agent = GlyphAgent("my_api_key", "https://vantage.local")
sealed = agent.seal_memory("Found arbitrage opportunity", "0xalice")
print(sealed["glyph"])  # Visualize the memory
```

### TypeScript Agent
```typescript
const glyphClient = {
  async sealMemory(agentKey: string, plaintext: string, owner: string) {
    const res = await fetch('https://vantage.local/api/glyphs/seal', {
      method: 'POST',
      headers: {
        'X-Agent-Key': agentKey,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ plaintext, owner, purpose: 'trading' })
    });
    return res.json();
  }
};

const sealed = await glyphClient.sealMemory('key', 'Price hit target', 'agent_x');
console.log(sealed.glyph);
```

---

## FAQ

**Q: Can I use the same passphrase across agents?**  
A: No. Each agent should have a unique passphrase or seed. Shared passphrases = shared keys.

**Q: How do I rotate keys?**  
A: Derive new keys with PBKDF2 + new owner context. Re-seal existing blobs with new keys.

**Q: Can two agents share a memory?**  
A: Not directly. Agent A seals → shares metadata (glyph, odu, owner). Agent B can verify integrity via Merkle root, but needs Agent A's consent to decrypt via `/open`.

**Q: What if GCM tag verification fails?**  
A: Blob is corrupted or tampered. Return error; do not decrypt.

**Q: Is the glyph deterministic?**  
A: Yes. Same plaintext → same canonical_id → same glyph. Collisions are cosmetic; canonical_id is the true address.

---

## See Also

- `OSOVM/GLYPHINDEX_SPEC.md` — Wire format specification
- `glyphindex/CONFORMANCE_KIT.md` — Multi-language testing
- `docs/GLYPHINDEX_KEYING.md` — Key derivation internals
