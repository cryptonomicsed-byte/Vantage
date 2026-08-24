"""Pluggable hook for wM/Fold-4's GenOffice document-generation skill.

2026-08-14 PLAN-LOCK with wM: DIRECT synchronous invocation, not the
engram/async path. Vantage's broadcast-intent flow (copilot.py's
_handle_intent, /api/copilot/execute) is already synchronous request/
response -- direct invocation matches that shape with no new moving
parts.

Real invocation surface, from wM 2026-08-14: two callable skills exist in
a local minipae clone (skills/de_package_docx/de_package_docx.py,
skills/de_deliver_client_artifact/de_deliver_client_artifact.py).

de_package_docx.generate_docx(template_path, output_path, paragraphs,
genoffice_repo=None) is called in-process (per wM's stated preference
over subprocess) -- it's a real function, not a network call, that
itself shells out to `npx tsx` against a local genspark-ai/genoffice
clone to run the real @genoffice/docx-engine. Confirmed live 2026-08-14
against a real genoffice clone: real template parsed, real paragraphs
appended, valid Word 2007+ OOXML output verified by unzipping
word/document.xml and finding the injected text.

2026-08-23, owner-authorized: de_deliver_client_artifact is now wired
too. It wraps de_package_docx and additionally writes a signed
delivery-receipt engram over NIP-AE (kind:30174) to NIPAE_RELAY
(defaults to relay.damus.io, minipae's own default and the address the
plan was "locked" against). This was held back earlier because it means
this process holds a live Nostr secret key (NIPAE_NSEC) and publishes
outside Vantage's own self-hosted relay -- a real posture change, not
just new code, so it waited for explicit authorization.

What actually goes over the wire, confirmed by reading minipae.py
(build_event/conversation_key) directly rather than assuming: every
engram is NIP-44-encrypted (ChaCha20, ECDH conversation key) to a single
owner pubkey before it reaches the relay. minipae has no discrete
private/followers/federated/public tiering the way Vantage's own
memory_vault.py does -- the closest analogue is "encrypted to one
pubkey" vs "plaintext" (e.g. Crucible claims), nothing in between.
NIPAE_OWNER is left unset by default, which makes deliver() encrypt to
its own pubkey: only whoever holds NIPAE_NSEC can ever decrypt the
receipt content (client name, parts, artifact size). The relay only
ever sees ciphertext plus routing metadata (kind, HMAC'd d-tag, pubkey,
timestamp) -- ✅that's the most restrictive mode this system supports,
not an open publish.

Same empty-means-disabled contract as every other pluggable hook in this
file family (osovm_client.py, bondhive_client.py): GENOFFICE_SKILLS_PATH
unset means every function here is a no-op, not a fabricated call to a
path nobody has confirmed exists on this host, and NIPAE_NSEC unset means
deliver_document() falls back to generate-only (no receipt), never a
fabricated publish.
"""
import asyncio
import importlib.util
import logging
import os
from typing import List, Optional

from .config import settings

logger = logging.getLogger(__name__)

_module_cache = None
_deliver_module_cache = None
_minipae_module_cache = None


def enabled() -> bool:
    return bool(settings.GENOFFICE_SKILLS_PATH) and bool(settings.GENOFFICE_REPO)


def delivery_enabled() -> bool:
    return enabled() and bool(settings.NIPAE_NSEC)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_de_package_docx():
    """Import skills/de_package_docx/de_package_docx.py as a module,
    without requiring it to be installed as a package or on sys.path
    permanently -- keeps this an optional, config-gated dependency."""
    global _module_cache
    if _module_cache is not None:
        return _module_cache

    skill_file = os.path.join(
        settings.GENOFFICE_SKILLS_PATH, "de_package_docx", "de_package_docx.py"
    )
    _module_cache = _load_module("de_package_docx", skill_file)
    return _module_cache


def _load_de_deliver_client_artifact():
    """Import skills/de_deliver_client_artifact/de_deliver_client_artifact.py
    and the minipae.py it depends on. Requires GENOFFICE_SKILLS_PATH to be
    <minipae repo>/skills -- de_deliver imports minipae.py from its repo
    root (three dirs up from the skill file) and de_package_docx as a
    sibling skill, both loaded here explicitly rather than relying on the
    skill file's own sys.path insertion (correct when run as a script,
    not guaranteed when exec'd via importlib from an arbitrary path)."""
    global _deliver_module_cache, _minipae_module_cache
    if _deliver_module_cache is not None:
        return _deliver_module_cache

    minipae_repo = os.path.dirname(settings.GENOFFICE_SKILLS_PATH.rstrip("/"))
    minipae_file = os.path.join(minipae_repo, "minipae.py")
    _minipae_module_cache = _load_module("minipae", minipae_file)

    import sys
    sys.modules.setdefault("minipae", _minipae_module_cache)
    sys.modules.setdefault("de_package_docx", _load_de_package_docx())

    skill_file = os.path.join(
        settings.GENOFFICE_SKILLS_PATH, "de_deliver_client_artifact", "de_deliver_client_artifact.py"
    )
    _deliver_module_cache = _load_module("de_deliver_client_artifact", skill_file)
    return _deliver_module_cache


async def generate_document(
    template_path: str, output_path: str, paragraphs: List[str]
) -> Optional[dict]:
    """Call de_package_docx.generate_docx() in-process.

    Returns None (not an error) when GENOFFICE_SKILLS_PATH/GENOFFICE_REPO
    are unset -- callers should treat that as "document generation
    unavailable", the same degrade-gracefully behavior every other
    pluggable hook here uses. Returns the skill's own result dict
    ({"ok": True, "output": ..., "bytes": ...}) on success, None on any
    failure (bad template, missing genoffice clone, node/npx error).

    Runs the (blocking, subprocess-shelling) skill call in a thread so it
    doesn't block the event loop -- generate_docx() itself calls
    subprocess.run() synchronously.
    """
    if not enabled():
        return None

    try:
        mod = _load_de_package_docx()
        result = await asyncio.to_thread(
            mod.generate_docx,
            template_path,
            output_path,
            paragraphs,
            settings.GENOFFICE_REPO,
        )
        return result
    except Exception as e:
        logger.warning("GenOffice (de_package_docx) invocation error: %s", e)
        return None


async def deliver_document(
    client: str, template_path: str, output_path: str, parts: List[str]
) -> Optional[dict]:
    """Call de_deliver_client_artifact.deliver() in-process: generates the
    real .docx (same underlying call as generate_document) and, since
    NIPAE_NSEC is configured, additionally writes+publishes a NIP-44
    encrypted delivery-receipt engram to NIPAE_RELAY.

    Returns None (not an error) when GENOFFICE_SKILLS_PATH/GENOFFICE_REPO/
    NIPAE_NSEC aren't all set -- callers should fall back to
    generate_document() for generation without a receipt. Returns
    {"artifact": {...}, "receipt": {"slug": ..., "published": ...}} on
    success.

    Runs in a thread for the same reason as generate_document: the skill
    itself is blocking (subprocess.run for docx generation, then its own
    internal asyncio.run() for the relay publish -- which requires no
    event loop already running in that thread, hence the thread).
    """
    if not delivery_enabled():
        return None

    try:
        mod = _load_de_deliver_client_artifact()
        m = _minipae_module_cache
        nsec = settings.NIPAE_NSEC.strip()
        sk = m.nsec_decode(nsec) if nsec.startswith("nsec1") else bytes.fromhex(nsec)
        agent_pub = m.pubkey_from_secret(int.from_bytes(sk, "big"))
        owner_hex = settings.NIPAE_OWNER.strip()
        owner = bytes.fromhex(owner_hex) if owner_hex else agent_pub

        result = await asyncio.to_thread(
            mod.deliver,
            client,
            template_path,
            output_path,
            parts,
            sk,
            owner,
            settings.NIPAE_RELAY,
            settings.GENOFFICE_REPO,
        )
        return result
    except Exception as e:
        logger.warning("GenOffice (de_deliver_client_artifact) invocation error: %s", e)
        return None
