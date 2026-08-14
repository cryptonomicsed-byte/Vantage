import hashlib as _hashlib
import logging
from pathlib import Path
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Check backend/.env first, then project root .env, then cwd .env
_candidates = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
    Path(".env"),
]
_ENV_FILE = next((str(p) for p in _candidates if p.exists()), str(_candidates[0]))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_prefix="VANTAGE_",
        env_file_encoding="utf-8",
    )

    APP_NAME: str = "Vantage"
    VERSION: str = "0.2.1"
    DEBUG: bool = False

    DATA_DIR: Path = Path("data")
    MEDIA_DIR: Path = Path("media/agents")
    WEBUI_DIR: Path = Path("/opt/ares/Vantage/frontend/dist")

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    PUBLIC_URL: str = "http://localhost:8000"

    # Postgres dual-backend (backend/db_adapter.py) -- empty POSTGRES_URL
    # means SQLite-only (current production default); setting it switches
    # get_db_connection() to a pooled Postgres connection instead. These
    # fields were referenced by db_adapter.py without ever being declared
    # here (a real pre-existing bug -- any code path touching the adapter
    # crashed with AttributeError, found via the e2e test audit).
    POSTGRES_URL: str = ""
    POSTGRES_POOL_MIN: int = 2
    POSTGRES_POOL_MAX: int = 10
    POSTGRES_POOL_TIMEOUT: int = 30

    # Real Tor hidden service mirror (see /etc/tor/torrc HiddenServiceDir
    # on the host) -- same general-resiliency purpose federation/mesh
    # already serves for discoverable, independent instances. Optional;
    # empty if no onion service is configured.
    ONION_URL: str = ""

    # Optional: POST publish events to any external webhook URL.
    # Leave empty to disable. No external service required.
    OUTBOUND_WEBHOOK_URL: str = ""

    ALLOWED_ORIGINS: List[str] = ["http://localhost:8000"]
    MAX_UPLOAD_MB: int = 500

    # Walrus decentralized storage (optional, set WALRUS_ENABLED=true to activate)
    WALRUS_ENABLED: bool = False
    WALRUS_PUBLISHER_URL: str = ""
    WALRUS_AGGREGATOR_URL: str = ""

    # Sui blockchain integration (optional)
    SUI_ENABLED: bool = False
    SUI_CONTRACT_ADDRESS: str = ""
    SUI_NODE_URL: str = "https://fullnode.mainnet.sui.io"

    # Seal encryption (optional)
    SEAL_ENABLED: bool = False

    # GlyphIndex sovereign memory (custodial-mode master secret; empty disables
    # the /api/glyphs router. Sovereign mode — client-sealed blobs — needs no key.)
    GLYPH_MASTER_SECRET: str = ""

    # ── Trading execution engine ──────────────────────────────────────────
    # The execution engine polls trading_orders for pending rows and runs them
    # through per-chain adapters. Two independent gates so wiring can be live
    # while real fund movement stays off until deliberately enabled:
    #   TRADING_ENGINE_ENABLED — run the background execution loop at all
    #   TRADING_LIVE_ENABLED   — actually sign+submit on-chain (else dry-run:
    #                            orders are marked 'ready' with the built intent)
    TRADING_ENGINE_ENABLED: bool = False
    TRADING_LIVE_ENABLED: bool = False
    TRADING_ENGINE_INTERVAL: int = 5  # seconds between pending-order polls

    # Solana / Jupiter execution
    HELIUS_API_KEY: str = ""  # Helius RPC key for quotes/submit/confirm

    # Composio (composio.dev) full tool-integration catalog -- ~1000 real
    # toolkits (Gmail, GitHub, Slack, Notion, Salesforce, etc), native SDK
    # (not hosted-MCP passthrough), see backend/routers/composio.py
    COMPOSIO_API_KEY: str = ""
    JUPITER_BASE_URL: str = "https://api.jup.ag/swap/v1"

    # Per-order and per-day safety caps (SOL). Deliberately conservative.
    TRADING_MAX_SOL_PER_ORDER: float = 0.01
    TRADING_DAILY_SOL_CAP: float = 0.1
    TRADING_MAX_CONCURRENT_PENDING: int = 5
    TRADING_MIN_LIQUIDITY_USD: float = 500.0
    TRADING_DEFAULT_SLIPPAGE_BPS: int = 300
    TRADING_COOLDOWN_SECONDS: int = 30  # min gap between two on-chain trades

    # ── Pump.fun scan → signal → order pipeline ───────────────────────────
    # When enabled, a loop polls GeckoTerminal trending, safety-filters, and
    # posts high-conviction signals to the ingest endpoint (which auto-creates
    # orders). Requires PUMPFUN_SCAN_AGENT_ID so signals attribute to a real
    # agent + wallet. Off by default — no autonomous trading without opt-in.
    PUMPFUN_SCAN_ENABLED: bool = False
    PUMPFUN_SCAN_AGENT_ID: int = 0
    PUMPFUN_SCAN_INTERVAL: int = 60  # seconds between trending scans
    PUMPFUN_SCAN_CONVICTION: float = 0.72  # >0.7 → auto-order in ingest
    PUMPFUN_MIN_VOLUME_USD: float = 5000.0
    PUMPFUN_MAX_TOP5_HOLDER_PCT: float = 40.0

    # Cross-instance federation (optional)
    FEDERATION_ENABLED: bool = True

    # Creation pipeline: Vantage only tracks job state — agents drive generation
    # using their own LLM, TTS, and image/video tools, then publish via standard endpoints.

    ADMIN_KEY: str = ""  # set via VANTAGE_ADMIN_KEY env var — no hardcoded default

    # Optional gate on /api/agents/register (unset by default = fully open,
    # unchanged behavior). Set VANTAGE_REGISTER_INVITE_TOKEN to require
    # callers to pass a matching invite_token in the registration body --
    # a real spam/abuse vector today (only a 5/min per-IP limit protects it).
    REGISTER_INVITE_TOKEN: str = ""

    @field_validator("ADMIN_KEY")
    @classmethod
    def validate_admin_key(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("VANTAGE_ADMIN_KEY must be at least 32 characters")
        return v

    # System tool tokens: narrowly-scoped auth for infrastructure daemons posting signals.
    # Each tool can ONLY POST to its own signal ingest endpoint.
    # Set via VANTAGE_TOOL_TRADING, VANTAGE_TOOL_SECURITY, VANTAGE_TOOL_INTEL env vars.
    TOOL_TRADING: str = ""  # freqtrade_bridge → /api/trading/signals/ingest
    TOOL_SECURITY: str = ""  # security_bridge, strix_runner, atomic_daemon → /api/security/scan-result
    TOOL_INTEL: str = ""     # worldmonitor_bridge, data feeds → /api/intel/signals/ingest

    # Master key for encrypting agents.sealed_seed_enc (Buzz/Nostr identity
    # seeds) at rest. Lives only in env/secrets-manager, never in the DB --
    # per-agent AES-256-GCM keys are HKDF-derived from this + agent_id, so a
    # DB compromise alone (without this env var) cannot recover any seed.
    # Set via VANTAGE_SEED_MASTER_KEY.
    SEED_MASTER_KEY: str = ""

    @field_validator("SEED_MASTER_KEY")
    @classmethod
    def validate_seed_master_key(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("VANTAGE_SEED_MASTER_KEY must be at least 32 characters if set")
        return v

    @field_validator("TOOL_TRADING", "TOOL_SECURITY", "TOOL_INTEL")
    @classmethod
    def validate_tool_keys(cls, v: str, info) -> str:
        if v and len(v) < 32:
            field_name = info.field_name
            raise ValueError(f"VANTAGE_{field_name} must be at least 32 characters if set")
        return v

    @property
    def ADMIN_KEY_HASH(self) -> Optional[str]:
        """SHA-256 of admin key, computed once. None if admin key is not set."""
        if not self.ADMIN_KEY:
            return None
        return _hashlib.sha256(self.ADMIN_KEY.encode()).hexdigest()

    # Federation peer-manifest trust: retired the shared FEDERATION_KEY HMAC
    # secret (2026-07-25) in favor of per-instance Nostr identity (BIP340
    # schnorr, TOFU-pinned pubkeys) -- see buzz_identity.derive_instance_keypair()
    # and GET /federation/identity. One compromised peer's key no longer
    # compromises the whole federation, which a shared secret could not offer.

    # Optional: OpenRouter API key — enables true vector semantic search in memory vault.
    # Falls back to wildcard FTS5 if not set. Set via VANTAGE_OPENROUTER_KEY env var.
    OPENROUTER_KEY: str = ""

    # Ọmọ Kọ́dà integration (Block Mesh)
    # STEWARD_URL: Vantage can push mesh events back to the Ọmọ Kọ́dà steward (optional).
    # MESH_KEY: shared secret for Ọmọ Kọ́dà→Vantage mesh calls (optional; falls back to X-Agent-Key).
    STEWARD_URL: str = ""
    MESH_KEY: str = ""
    LLM_KEY: str = ""  # Fernet key for agent LLM API key encryption

    # Code pipeline: Gitea hosting for agent-pushed repos (optional; push/scan
    # endpoints 503 if unset).
    GITEA_URL: str = ""
    GITEA_TOKEN: str = ""

    # Strix security scanning (github.com/usestrix/strix) runs on a small
    # standalone runner on the VPS host, not inside this container (no Docker
    # access here by design). Empty URL disables the strix scan engine — the
    # existing fast regex scan keeps working either way.
    STRIX_RUNNER_URL: str = ""

    # supermemory (self-hosted, optional) — memory/context ingestion for the
    # code pipeline. Empty URL makes the memory-ingest endpoint a no-op.
    SUPERMEMORY_URL: str = ""
    SUPERMEMORY_API_KEY: str = ""

    # Parrot security scan gate (ClamAV/YARA/binwalk container) for uploaded
    # artifacts. Unlike the enrichment sidecars above, this one fails CLOSED:
    # if set but unreachable, uploads are rejected rather than silently passed
    # through. Empty URL disables the gate entirely (pre-existing behavior).
    PARROT_SECURITY_URL: str = ""

    # Ọmọ Kọ́dà sovereign-agent kernel (Rust). When set, Vantage can birth
    # Omo-Koda agents (POST /api/agents/birth-omokoda proxies to its /v1/birth)
    # and push published broadcasts into its knowledge vault. Empty = disabled
    # (the broadcast-push in agents.py becomes a no-op). Referenced as
    # settings.OMOKODA_URL — must exist here so that access never AttributeErrors.
    OMOKODA_URL: str = ""

    # Shared, kernel-wide bearer token for Omo-Koda2's /v1/cognition webhook
    # (Authorization: Bearer <token>) -- confirmed live with the Omo-Koda2
    # session: NOT per-agent, one value proves the caller may hit the
    # endpoint at all; agent_id/agent_key in the request body select WHICH
    # agent. Only used by the Omo-Koda2 auto-link convenience path -- a
    # generic third-party cognition_url stores its own token per-agent in
    # agents.cognition_auth_token instead.
    OMOKODA_COGNITION_TOKEN: str = ""

    # OmniRoute -- free OpenAI-compatible AI gateway Omo-Koda2's kernel
    # already uses (default localhost:8300, same host, no auth needed
    # locally -- confirmed live). Copilot's default LLM fallback for any
    # agent with no cognition_url of its own, so chat is never JUST the
    # regex parser unless OmniRoute itself is unreachable.
    OMNIROUTE_URL: str = "http://localhost:8300"
    OMNIROUTE_MODEL: str = "auto"

    # OSOVM (Proof-of-Simulation VM, Sui/Move settlement) -- when set,
    # Vantage can ask OSOVM to attest a completed job_task with a real
    # determinism proof (sim hash / proof id) before it's approved/paid out.
    # Empty = disabled: osovm_client.py's calls become no-ops and
    # job_tasks.osovm_proof_id simply stays NULL, exactly like OMOKODA_URL's
    # empty-means-disabled contract above. No code coupling existed between
    # OSOVM and Vantage before this -- this is the pluggable hook, not a
    # claim that a live OSOVM endpoint is reachable from here yet.
    OSOVM_URL: str = ""
    OSOVM_API_KEY: str = ""

    # Bondhive (Solana/Anchor stake+slash reputation system, real Schnorr
    # signing, BondScore). Empty = disabled: bondhive_client.py's calls
    # become no-ops and agents.bondhive_stake_account stays NULL. Vantage's
    # own BlockMesh trust/reputation system (/api/mesh/trust/*) is separate
    # and NOT reconciled with BondScore by this change -- an agent can have
    # a Vantage trust score and a Bondhive stake account that disagree.
    # Wiring this hook does not resolve that; it only makes the connection
    # possible to build once the reconciliation decision is made.
    BONDHIVE_RPC_URL: str = ""
    BONDHIVE_PROGRAM_ID: str = ""

    # GenOffice document-generation adapter (wM/Fold-4 coordination,
    # 2026-08-14 PLAN-LOCK: direct synchronous invocation, not the
    # engram/async path -- that would depend on minipae's cross-relay
    # bridge, which is explicitly not wired yet, see
    # Vantage-to-wM_minipae_relay_check). wM's de_package_docx skill is
    # "invocable as Python function or CLI", not yet exposed as a network
    # service -- so this hook is a subprocess CLI invocation, same
    # empty-means-disabled contract as every other pluggable hook here.
    # Command template gets {template_path}/{output_path}/{paragraphs_json}
    # substituted in; empty = disabled, generate_document intent no-ops.
    GENOFFICE_INVOKE_CMD: str = ""


settings = Settings()

# Runtime guard: warn and override any Phase C feature flags that are not yet implemented.
# Setting these to True would cause crashes; this guard prevents silent misconfig.
_logger = logging.getLogger(__name__)
_UNIMPLEMENTED_FLAGS = {
    "WALRUS_ENABLED": settings.WALRUS_ENABLED,
    "SUI_ENABLED": settings.SUI_ENABLED,
    "SEAL_ENABLED": settings.SEAL_ENABLED,
    # FEDERATION_ENABLED removed 2026-07-25: the feature is fully
    # implemented (gossip loop, peer CRUD, feed/ask aggregation, now
    # Nostr-backed trust) -- this guard was stale, left over from before
    # the feature was actually built. Re-enabled for real.
}
for _flag, _val in _UNIMPLEMENTED_FLAGS.items():
    if _val:
        _logger.warning(
            "%s=True but this feature is not yet implemented — setting to False",
            _flag,
        )
        object.__setattr__(settings, _flag, False)
