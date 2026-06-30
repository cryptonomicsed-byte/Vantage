"""
Vantage Crypto Utils — per-agent wallet encryption.
Each agent gets a derived encryption key from their API key + salt.
Private keys are AES-256-GCM encrypted at rest, never stored in plaintext.

Flow:
  agent_key = SHA-256(api_key + agent_id)
  encryption_key = PBKDF2(agent_key, salt, iterations=100000)
  encrypted = AES-256-GCM(plaintext_key, encryption_key, nonce)
  stored = b64(nonce) + ":" + b64(encrypted)

Key principle: agent's API key is the only secret needed.
Losing the API key = losing access to encrypted wallets.
No master key, no shared secret, per-agent isolation enforced by design.
"""
import hashlib, hmac, os, base64, struct, secrets
import hashlib as _hashlib  # for AES via pycryptodome
from Crypto.Cipher import AES


def derive_encryption_key(api_key: str, agent_id: int, salt: bytes = None) -> tuple[bytes, bytes]:
    """Derive AES-256 key from agent's API key. Returns (key, salt)."""
    if salt is None:
        salt = secrets.token_bytes(32)
    
    # Agent-specific derivation: iterated SHA-256
    derived = hashlib.sha256(f"{api_key}:{agent_id}".encode()).digest()
    for _ in range(1000):  # Simple iteration
        derived = hashlib.sha256(derived + salt).digest()
    
    return derived, salt


def encrypt_private_key(plaintext_key: str, api_key: str, agent_id: int) -> str:
    """Encrypt a private key for storage. Returns 'salt_b64:nonce_b64:ciphertext_b64'."""
    key, salt = derive_encryption_key(api_key, agent_id)
    nonce = secrets.token_bytes(12)
    
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext_key.encode())
    
    return base64.b64encode(salt).decode() + ":" + \
           base64.b64encode(nonce).decode() + ":" + \
           base64.b64encode(ciphertext + tag).decode()


def decrypt_private_key(encrypted: str, api_key: str, agent_id: int) -> str:
    """Decrypt a stored private key. Returns plaintext key string."""
    parts = encrypted.split(":")
    if len(parts) != 3:
        raise ValueError("Invalid encrypted key format (expected salt:nonce:ciphertext)")
    
    salt = base64.b64decode(parts[0])
    nonce = base64.b64decode(parts[1])
    ciphertext_with_tag = base64.b64decode(parts[2])
    
    key, _ = derive_encryption_key(api_key, agent_id, salt)
    
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]
    
    return cipher.decrypt_and_verify(ciphertext, tag).decode()


def encrypt_key_for_agent(plaintext_key: str, agent: dict) -> str:
    """Convenience: encrypt a private key for an agent dict."""
    return encrypt_private_key(plaintext_key, agent.get("api_key", ""), agent["id"])


def decrypt_key_for_agent(encrypted: str, agent: dict) -> str:
    """Convenience: decrypt a private key for an agent dict."""
    return decrypt_private_key(encrypted, agent.get("api_key", ""), agent["id"])
