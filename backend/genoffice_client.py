"""Pluggable hook for wM/Fold-4's GenOffice document-generation skill.

2026-08-14 PLAN-LOCK with wM: DIRECT synchronous invocation, not the
engram/async path. Vantage's broadcast-intent flow (copilot.py's
_handle_intent, /api/copilot/execute) is already synchronous request/
response -- direct invocation matches that shape with no new moving
parts. Async-via-engram was the explicit alternative on the table, but
depends on minipae's cross-relay bridge landing first (Vantage's own
NIP-AE mirror publishes to omokoda.duckdns.org:3443; minipae's proven
roundtrip is on relay.damus.io -- different relays, not interoperable
today, see Vantage-to-wM_minipae_relay_check-20260814T015213Z).
Async-via-engram remains the natural later upgrade once Phase 1 lands.

Real invocation surface, from wM 2026-08-14: two callable skills exist in
a local minipae clone (skills/de_package_docx/de_package_docx.py,
skills/de_deliver_client_artifact/de_deliver_client_artifact.py). Only
de_package_docx is wired here -- de_deliver_client_artifact additionally
writes a delivery-receipt engram via NIPAE_NSEC/NIPAE_RELAY (defaults to
relay.damus.io), which is exactly the cross-relay dependency Vantage is
holding off on; wiring it would mean this process holding a live Nostr
key and publishing outside the locked plan without separate
authorization. Not done here.

de_package_docx.generate_docx(template_path, output_path, paragraphs,
genoffice_repo=None) is called in-process (per wM's stated preference
over subprocess) -- it's a real function, not a network call, that
itself shells out to `npx tsx` against a local genspark-ai/genoffice
clone to run the real @genoffice/docx-engine. Confirmed live 2026-08-14
against a real genoffice clone: real template parsed, real paragraphs
appended, valid Word 2007+ OOXML output verified by unzipping
word/document.xml and finding the injected text.

Same empty-means-disabled contract as every other pluggable hook in this
file family (osovm_client.py, bondhive_client.py): GENOFFICE_SKILLS_PATH
unset means every function here is a no-op, not a fabricated call to a
path nobody has confirmed exists on this host. Production activation
requires deploying the minipae skills directory + a genoffice clone +
node/npx to wherever Vantage's backend actually runs (not done as part
of this change -- these paths exist locally where this was developed and
tested, not yet on the production VPS).
"""
import asyncio
import importlib.util
import logging
import os
from typing import List, Optional

from .config import settings

logger = logging.getLogger(__name__)

_module_cache = None


def enabled() -> bool:
    return bool(settings.GENOFFICE_SKILLS_PATH) and bool(settings.GENOFFICE_REPO)


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
    spec = importlib.util.spec_from_file_location("de_package_docx", skill_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache = mod
    return mod


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
