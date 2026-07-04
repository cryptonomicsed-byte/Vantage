"""
parrot-security scanner — a small standalone service that runs ClamAV, a
custom YARA ruleset, and binwalk against a single uploaded file on behalf of
Vantage's upload gate (backend/utils.py::_security_scan_and_normalize).

Deliberately NOT a dispatch/poll service like ops/strix-runner: scanning one
file with clamscan/yara/binwalk takes seconds, not minutes, and needs no
Docker-daemon access — so a single synchronous POST /scan is the right shape
here (same as the pine-runtime sandbox), not the async run_id pattern Strix
needs.

Endpoints:
  GET  /health
  POST /scan   multipart {file, kind} -> {clean, findings, risk_score}
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("parrot-security")

app = FastAPI(title="Parrot Security Scanner")

RULES_DIR = Path(os.environ.get("PARROT_RULES_DIR", "/app/rules"))
SCAN_TIMEOUT_SEC = int(os.environ.get("PARROT_SCAN_TIMEOUT_SEC", "60"))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "parrot-security"}


@app.post("/scan")
async def scan(file: UploadFile = File(...), kind: str = Form("")):
    content = await file.read()
    findings: list = []

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / (file.filename or "artifact")
        target.write_bytes(content)

        findings += _run_clamscan(target)
        findings += _run_yara(target)
        findings += _run_binwalk(target)

    clean = len(findings) == 0
    risk_score = min(1.0, 0.34 * len(findings))
    return {"clean": clean, "findings": findings, "risk_score": risk_score}


def _run_clamscan(target: Path) -> list:
    try:
        proc = subprocess.run(
            ["clamscan", "--no-summary", str(target)],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT_SEC,
        )
        if proc.returncode == 1:  # 1 = virus found, 0 = clean, 2 = scan error
            return [{"tool": "clamav", "detail": proc.stdout.strip()}]
        if proc.returncode not in (0, 1):
            logger.warning("clamscan error: %s", proc.stderr.strip())
        return []
    except FileNotFoundError:
        logger.warning("clamscan not installed — skipping AV check")
        return []
    except Exception as exc:
        logger.warning("clamscan failed: %s", exc)
        return []


def _run_yara(target: Path) -> list:
    if not RULES_DIR.exists() or not any(RULES_DIR.glob("*.yar*")):
        return []
    findings: list = []
    try:
        for rule_file in RULES_DIR.glob("*.yar*"):
            proc = subprocess.run(
                ["yara", str(rule_file), str(target)],
                capture_output=True, text=True, timeout=SCAN_TIMEOUT_SEC,
            )
            if proc.stdout.strip():
                findings.append({
                    "tool": "yara", "rule_file": rule_file.name,
                    "detail": proc.stdout.strip(),
                })
    except FileNotFoundError:
        logger.warning("yara not installed — skipping YARA check")
    except Exception as exc:
        logger.warning("yara failed: %s", exc)
    return findings


def _run_binwalk(target: Path) -> list:
    """Flags embedded/appended files inside what should be a single-format
    artifact — the polyglot signal (e.g. a GIF with a PHP payload appended)."""
    try:
        proc = subprocess.run(
            ["binwalk", str(target)],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT_SEC,
        )
        lines = [l for l in proc.stdout.splitlines() if l and not l.startswith(("DECIMAL", "-"))]
        # A clean single-format file still produces one header line for its
        # own signature; more than one distinct embedded signature is the tell.
        if len(lines) > 1:
            return [{"tool": "binwalk", "detail": "\n".join(lines[:20])}]
        return []
    except FileNotFoundError:
        logger.warning("binwalk not installed — skipping embedded-file check")
        return []
    except Exception as exc:
        logger.warning("binwalk failed: %s", exc)
        return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PARROT_PORT", "9878")))
