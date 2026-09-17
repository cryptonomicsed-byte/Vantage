"""Per-platform MCP connector profiles.

Why this file exists: platforms disagree about how OAuth should be *performed*
even when they agree on the protocol.

  * ChatGPT identifies itself with a Client ID Metadata Document (CIMD) - it
    sends an HTTPS URL as its client_id - and redirects to
    https://chatgpt.com/connector/oauth/{callback_id}.
  * Codex uses loopback redirects (RFC 8252) on a port chosen at login, derives
    {callback_id} from the MCP server URL, and will not exchange a code whose
    returned `iss` does not match.
  * A CLI or a single-board computer may only be able to send a static header.

One shared token authority (oauth_store.py) + one profile per platform here.
Adding a platform means adding a profile and proving it with a real connection
test -- it must never mean adding another place that mints credentials.

PRECISION NOTE: the `chatgpt` and `codex` profiles below are written from
OpenAI's published connector requirements (redirect shapes, CIMD, `iss`
handling, offline_access). The `claude` and `grok` profiles are BEST-EFFORT
placeholders -- their exact redirect URIs have not been verified against live
documentation, so each is marked verified=False and must be confirmed by
actually connecting that platform before it is trusted. Do not move
verified=True on a profile without a real end-to-end connection behind it.
"""
import logging
import re
from typing import Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Scopes. Coarse on purpose: MCP tool-level nuance is expressed with per-tool
# securitySchemes later, not by proliferating scopes now.
SCOPE_READ = "vantage.read"
SCOPE_WRITE = "vantage.write"
SCOPE_ACCOUNT = "vantage.account"      # may create/link its own Vantage agent
SCOPE_OFFLINE = "offline_access"       # refresh tokens; ChatGPT needs this advertised

BASE_SCOPES = [SCOPE_READ, SCOPE_WRITE, SCOPE_ACCOUNT]


def _host_of(uri: str) -> str:
    try:
        return (urlparse(uri).hostname or "").lower()
    except Exception:
        return ""


def _https_on(domain: str) -> Callable[[str], bool]:
    def check(uri: str) -> bool:
        p = urlparse(uri)
        if p.scheme != "https":
            return False
        h = (p.hostname or "").lower()
        return h == domain or h.endswith("." + domain)
    return check


def _chatgpt_redirect(uri: str) -> bool:
    """ChatGPT's connector callback. The exact path is
    /connector/oauth/{callback_id} where callback_id is derived from the MCP
    server URL, so the id segment is opaque to us -- but the host and the
    /connector/ prefix are not, and are enforced."""
    if not _https_on("chatgpt.com")(uri):
        return False
    p = urlparse(uri)
    return p.path.startswith("/connector/oauth/") or p.path.startswith("/connector")


def _loopback_redirect(uri: str) -> bool:
    """RFC 8252 native-app callback. Host must be a literal loopback address --
    NOT 'localhost' (a DNS name that can be re-pointed) and not a LAN IP. The
    port is deliberately unconstrained: Codex picks it at login time."""
    p = urlparse(uri)
    if p.scheme != "http":
        return False
    return (p.hostname or "") in ("127.0.0.1", "::1")


def _claude_redirect(uri: str) -> bool:
    """UNVERIFIED. Anthropic's hosted MCP client callback; confirm on connect."""
    return _https_on("claude.ai")(uri) or _https_on("anthropic.com")(uri)


def _grok_redirect(uri: str) -> bool:
    """UNVERIFIED. Confirm on connect before trusting."""
    return _https_on("x.ai")(uri) or _https_on("grok.com")(uri)


def _cimd_client_id(domain: str) -> Callable[[str], bool]:
    """A CIMD client_id is an HTTPS URL at the platform that documents the
    client. Validating the host stops a random attacker-supplied URL from
    being treated as ChatGPT's stable client identity."""
    def check(client_id: str) -> bool:
        return client_id.startswith("https://") and _https_on(domain)(client_id)
    return check


PROFILES: dict[str, dict] = {
    "chatgpt": {
        "display": "ChatGPT",
        "verified": True,
        "allow_cimd": True,
        "allow_dcr": True,
        "allow_predefined": True,
        "cimd_host_check": _cimd_client_id("chatgpt.com"),
        "redirect_check": _chatgpt_redirect,
        "scopes": BASE_SCOPES + [SCOPE_OFFLINE],
        "consent_title": "Connect Vantage to ChatGPT",
        "consent_blurb": (
            "ChatGPT will be able to act on Vantage as the agent you choose "
            "below — publishing, taking guild work, and using the full Vantage "
            "tool surface."
        ),
        "notes": (
            "ChatGPT prefers CIMD and falls back to DCR. It attaches the access "
            "token as Authorization: Bearer on every MCP request. offline_access "
            "must stay advertised or ChatGPT loses access at access-token expiry "
            "and users re-authenticate constantly."
        ),
    },
    "codex": {
        "display": "Codex",
        "verified": True,
        "allow_cimd": True,
        "allow_dcr": True,
        "allow_predefined": True,
        "cimd_host_check": _cimd_client_id("chatgpt.com"),
        "redirect_check": _loopback_redirect,
        "scopes": BASE_SCOPES + [SCOPE_OFFLINE],
        "consent_title": "Connect Vantage to Codex",
        "consent_blurb": (
            "Codex will act on Vantage as the agent you choose below, over a "
            "loopback callback on your own machine."
        ),
        "notes": (
            "Loopback redirect with a port chosen at login -- host+path must "
            "match while the port varies. Advertise "
            "authorization_response_iss_parameter_supported only once `iss` is "
            "actually emitted, because Codex hard-fails the exchange on a "
            "mismatch. callback_id is derived from the MCP server URL, so "
            "/mcp and /mcp/guild get different ids."
        ),
    },
    "claude": {
        "display": "Claude",
        "verified": False,
        "allow_cimd": False,
        "allow_dcr": True,
        "allow_predefined": True,
        "cimd_host_check": None,
        "redirect_check": _claude_redirect,
        "scopes": BASE_SCOPES + [SCOPE_OFFLINE],
        "consent_title": "Connect Vantage to Claude",
        "consent_blurb": "Claude will act on Vantage as the agent you choose below.",
        "notes": "UNVERIFIED redirect shape. Confirm by connecting before trusting.",
    },
    "grok": {
        "display": "Grok",
        "verified": False,
        "allow_cimd": False,
        "allow_dcr": True,
        "allow_predefined": True,
        "cimd_host_check": None,
        "redirect_check": _grok_redirect,
        "scopes": BASE_SCOPES + [SCOPE_OFFLINE],
        "consent_title": "Connect Vantage to Grok",
        "consent_blurb": "Grok will act on Vantage as the agent you choose below.",
        "notes": "UNVERIFIED redirect shape. Confirm by connecting before trusting.",
    },
    "generic": {
        "display": "MCP client",
        "verified": True,
        "allow_cimd": False,
        "allow_dcr": True,
        "allow_predefined": True,
        "cimd_host_check": None,
        "redirect_check": None,   # any https URI registered through DCR
        "scopes": BASE_SCOPES + [SCOPE_OFFLINE],
        "consent_title": "Connect to Vantage",
        "consent_blurb": "This application will act on Vantage as the agent you choose below.",
        "notes": "Fallback profile for any standards-compliant MCP client.",
    },
}

DEFAULT_PROFILE = "generic"


def get_profile(platform: Optional[str]) -> dict:
    return PROFILES.get(platform or "", PROFILES[DEFAULT_PROFILE])


def detect_platform(client_id: str = "", redirect_uri: str = "") -> str:
    """Best-effort platform identification for token/audit labelling. Never an
    authorization decision -- the redirect allowlist below is what actually
    gates the flow."""
    cid = client_id or ""
    ruri = redirect_uri or ""
    if "chatgpt.com/connector/oauth" in ruri or "connector_platform_oauth_redirect" in ruri:
        # ChatGPT's hosted connector vs Codex's loopback both cite chatgpt.com
        # CIMD documents; the redirect is what separates them.
        return "chatgpt"
    if _loopback_redirect(ruri):
        return "codex"
    if "chatgpt.com" in cid and ruri:
        return "chatgpt"
    if _claude_redirect(ruri):
        return "claude"
    if _grok_redirect(ruri):
        return "grok"
    return DEFAULT_PROFILE


def validate_redirect(platform: str, redirect_uri: str) -> bool:
    """A redirect_uri that is not on the platform's allowlist is the classic
    authorization-code leak: whoever controls it receives the user's code. This
    is the single most security-relevant check in the flow."""
    if not redirect_uri:
        return False
    prof = get_profile(platform)
    check = prof.get("redirect_check")
    if check is None:
        # Generic profile: https only, no fragments, no credentials in the URL.
        p = urlparse(redirect_uri)
        return (p.scheme == "https" and bool(p.hostname)
                and not p.fragment and not p.username)
    try:
        return bool(check(redirect_uri))
    except Exception as exc:
        logger.warning("redirect check failed for %s: %s", platform, exc)
        return False


def validate_client_id(platform: str, client_id: str) -> bool:
    """Guards CIMD: an arbitrary https URL must not be accepted as a platform's
    client identity just because it is a URL."""
    prof = get_profile(platform)
    if not client_id:
        return False
    if client_id.startswith("https://"):
        check = prof.get("cimd_host_check")
        if check is None:
            return False   # no CIMD for this platform
        try:
            return bool(check(client_id))
        except Exception:
            return False
    # Locally-issued ids (DCR or predefined) look like vgcl_...
    return bool(re.fullmatch(r"vgcl_[A-Za-z0-9_\-]{8,}", client_id))


def client_registration_modes(platform: str) -> list[str]:
    prof = get_profile(platform)
    modes = []
    if prof.get("allow_cimd"):
        modes.append("cimd")
    if prof.get("allow_dcr"):
        modes.append("dcr")
    if prof.get("allow_predefined"):
        modes.append("predefined")
    return modes


def scopes_for(platform: str) -> list[str]:
    return list(get_profile(platform).get("scopes") or BASE_SCOPES)


def redirect_allowed(client: dict, platform: str, redirect_uri: str) -> bool:
    """Both halves of the redirect check, and you need both.

    1. The platform *pattern* (validate_redirect) -- stops https://evil.com
       being used against a ChatGPT-typed client.
    2. The *registered* list -- stops a client that registered a benign URI from
       then requesting authorization against a different one it controls. Without
       this, DCR registration is decorative: the attacker registers
       https://their-app.com/cb legitimately, then redirects the victim's code to
       any https host they like.

    Loopback URIs match on host+path with the port ignored, because RFC 8252
    native clients pick their port at login time.
    """
    if not validate_redirect(platform, redirect_uri):
        return False

    registered = [u for u in (client.get("redirect_uris") or []) if isinstance(u, str)]
    if not registered:
        # A CIMD document that disclosed no redirect_uris leaves only the
        # platform pattern to go on; anything else with no registered URI is
        # refused rather than guessed at.
        return client.get("registration_method") == "cimd"

    pu = urlparse(redirect_uri)
    for reg in registered:
        if reg == redirect_uri:
            return True
        pr = urlparse(reg)
        if (pr.scheme == pu.scheme and pr.hostname == pu.hostname
                and pr.path == pu.path
                and (pu.hostname or "") in ("127.0.0.1", "::1")):
            return True
    return False


def resolve_scopes(platform: str, requested: Optional[str]) -> str:
    """Never grant more than the profile allows, and never echo back a scope the
    platform was not offered. Unknown scopes are dropped rather than rejected so
    a client asking for something extra still gets a usable token."""
    allowed = set(scopes_for(platform))
    asked = [s for s in (requested or "").split() if s]
    if not asked:
        asked = [SCOPE_READ, SCOPE_WRITE, SCOPE_ACCOUNT]
    granted = [s for s in asked if s in allowed]
    if not granted:
        granted = [SCOPE_READ]
    return " ".join(dict.fromkeys(granted))
