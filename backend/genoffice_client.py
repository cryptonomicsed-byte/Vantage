"""Pluggable hook for wM/Fold-4's GenOffice document-generation skill
(de_package_docx, registered in skills/catalog.yaml under verb de_package).

2026-08-14 PLAN-LOCK with wM: DIRECT synchronous invocation, not the
engram/async path. Vantage's broadcast-intent flow (copilot.py's
_handle_intent, /api/copilot/execute) is already synchronous request/
response -- direct invocation matches that shape with no new moving
parts. Async-via-engram (adapter writes intent to mem/vantage/*, wM's
side watches and invokes, writes a result engram back) was the explicit
alternative on the table, but depends on minipae's cross-relay bridge
landing first (Vantage's own NIP-AE mirror publishes to
omokoda.duckdns.org:3443; minipae's proven roundtrip is on
relay.damus.io -- different relays, not interoperable today, see
Vantage-to-wM_minipae_relay_check-20260814T015213Z). Direct invocation
has no such dependency, so it's the correct shape for now; async-via-
engram remains the natural later upgrade once Phase 1 lands.

Real schema, confirmed by wM, live-verified on their side (real docx
template parsed, real paragraphs appended, structurally-valid output):
    {template_path: string, output_path: string, paragraphs: string[]}

Same empty-means-disabled contract as every other pluggable hook in this
file family (osovm_client.py, bondhive_client.py): GENOFFICE_INVOKE_CMD
unset means every function here is a no-op, not a fabricated call to a
CLI path nobody has confirmed exists on this host. de_package_docx is
"invocable as Python function or CLI" on wM's side -- not yet exposed as
a network service reachable from Vantage's VPS, so this is a subprocess
invocation, not an HTTP client like the OSOVM/Bondhive hooks.
"""
import asyncio
import json
import logging
import shlex
from typing import List, Optional

from .config import settings

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.GENOFFICE_INVOKE_CMD)


async def generate_document(
    template_path: str, output_path: str, paragraphs: List[str]
) -> Optional[dict]:
    """Invoke de_package_docx with the confirmed schema.

    Returns None (not an error) when GENOFFICE_INVOKE_CMD is unset --
    callers should treat that as "document generation unavailable", the
    same degrade-gracefully behavior every other pluggable hook here
    uses. Returns {"output_path": ..., "raw_stdout": ...} on success,
    None on any failure (bad template, invoke error, non-zero exit).
    """
    if not enabled():
        return None

    payload = {
        "template_path": template_path,
        "output_path": output_path,
        "paragraphs": paragraphs,
    }
    payload_json = json.dumps(payload)

    try:
        # Command template is an operator-configured local invocation
        # (e.g. "python3 /path/to/de_package_docx.py"), never built from
        # request data -- only the JSON payload is passed as a single
        # argument, so there's no shell-injection surface from
        # template_path/output_path/paragraphs content.
        cmd = shlex.split(settings.GENOFFICE_INVOKE_CMD) + [payload_json]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

        if proc.returncode != 0:
            logger.warning(
                "GenOffice invocation failed (exit %s): %s",
                proc.returncode, stderr.decode(errors="replace")[:300],
            )
            return None

        return {"output_path": output_path, "raw_stdout": stdout.decode(errors="replace")}
    except Exception as e:
        logger.warning("GenOffice invocation error: %s", e)
        return None
