#!/usr/bin/env python3
"""Transform agents.py: replace inline helpers with imports from new modules.

Usage:  python3 transform_agents.py [--dry-run]
"""
import ast
import sys

DRY_RUN = "--dry-run" in sys.argv

SRC = "backend/agents.py"

NEW_HEADER = """\
import asyncio
import hashlib as _hashlib
import json as _json
import logging
import os
import secrets
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import aiosqlite
import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

from .config import settings
from .db import DB_PATH, MEDIA_ROOT, init_agents_db
from .deps import get_agent, get_admin, _parse_body, _update_last_seen, _log_agent_activity
from .utils import (
    _log_buffer, _BufferHandler,
    _feed_clients, _gossip_channels, _broadcast_gossip, notify_feed_clients,
    _VALID_WEBHOOK_EVENTS, _fire_webhooks,
    _SEVERITY_MAP, _append_receipt,
    _VIDEO_MAGIC, _AUDIO_MAGIC, _IMAGE_MAGIC, _validate_file_magic,
    _notify_webhook, _check_token_milestones, _MILESTONES,
    _save_thumbnail,
    _ensure_messages_table, _create_notification,
    _check_dead_letter,
)

logger = logging.getLogger(__name__)
logging.getLogger().addHandler(_BufferHandler())

router = APIRouter(prefix="/api/agents", tags=["agents"])
"""

with open(SRC, "r") as f:
    lines = f.readlines()

print(f"Read {len(lines)} lines from {SRC}")

# ── Verify boundary content before committing ──────────────────────────────
BOUNDARIES = [
    (1080, "BLANK",                 "blank after _validate_file_magic (last removed in skip-1)"),
    (1081, "BLANK",                 "blank before Background processing section (first kept)"),
    (1214, "            pass", "last pass in _process_broadcast (last kept before skip-2)"),
    (1215, "BLANK",                  "line 1216 = first blank in skip-2 (first removed)"),
    (1269, "_VALID_JOB_STATUSES", "first kept after skip-2"),
    (1797, "_THUMB_EXTS",         "first removed in skip-3"),
    (1816, "BLANK",                  "last removed in skip-3"),
    (1817, "BLANK",                  "first kept after skip-3 (blank)"),
    (2757, "async def _ensure",   "first removed in skip-4"),
    (2787, "BLANK",                  "last removed in skip-4"),
    (2788, "BLANK",                  "first kept after skip-4"),
    (6128, "# ── Feature 3",      "first removed in skip-5"),
    (6162, "logger.warning",      "last removed in skip-5"),
    (6163, "BLANK",                  "first kept after skip-5"),
]

ok = True
for idx, expected_fragment, desc in BOUNDARIES:
    actual = lines[idx].rstrip("\n")
    if expected_fragment == "BLANK":
        if actual.strip() != "":
            print(f"  MISMATCH line {idx+1} ({desc}): expected blank, got {actual!r}")
            ok = False
        else:
            print(f"  OK line {idx+1} ({desc})")
    elif expected_fragment not in actual:
        print(f"  MISMATCH line {idx+1} ({desc}): expected {expected_fragment!r} in {actual!r}")
        ok = False
    else:
        print(f"  OK line {idx+1} ({desc})")

if not ok:
    print("\\nBoundary mismatch — aborting")
    sys.exit(1)

# ── Build new content ──────────────────────────────────────────────────────
new_parts = [
    NEW_HEADER,
    "".join(lines[1081:1215]),   # blank line + Background proc comment + _process_broadcast
    "\n\n",                       # separator before _VALID_JOB_STATUSES
    "".join(lines[1269:1797]),   # _VALID_JOB_STATUSES through blank before _THUMB_EXTS
    "".join(lines[1817:2757]),   # post-_save_thumbnail through blank before _ensure_messages_table
    "".join(lines[2788:6128]),   # post-_create_notification through blank before dead-letter comment
    "".join(lines[6163:]),       # post-_check_dead_letter to end of file
]
new_content = "".join(new_parts)

# ── Validate: must parse cleanly as Python ────────────────────────────────
try:
    ast.parse(new_content)
    print("\\nAST parse: OK")
except SyntaxError as e:
    print(f"\\nAST parse FAILED: {e}")
    sys.exit(1)

new_lines = len(new_content.splitlines())
print(f"New file: {new_lines} lines (was {len(lines)})")
print(f"Removed ~{len(lines) - new_lines} lines")

if DRY_RUN:
    print("\\nDry-run — not writing file")
    sys.exit(0)

with open(SRC, "w") as f:
    f.write(new_content)

print(f"\\nWrote {SRC}")
