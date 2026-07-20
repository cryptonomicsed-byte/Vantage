"""Per-agent memory vault: an Open Knowledge Format (OKF v0.1) bundle on disk,
with galaxy spatial indexing layered on top as producer-defined extension keys.

OKF (https://github.com/... — spec vendored conceptually, no registry needed)
says a knowledge bundle is just a directory of markdown files with YAML
frontmatter: every concept file has a REQUIRED `type` (a short descriptive
string — "Trade", "Skill", "Knowledge Triple" — never centrally registered),
and RECOMMENDED `title`/`description`/`resource`/`tags`/`timestamp`. Two
filenames are reserved at any level of the hierarchy: `index.md` (directory
listing, frontmatter forbidden except an optional `okf_version` at bundle
root) and `log.md` (dated update history, newest first).

Vantage's galaxy renderer needs additional per-note data (3D coordinates,
color, size) that has nothing to do with OKF — those live as ordinary
producer-defined extension keys (OKF §4.1 explicitly allows this) prefixed
`galaxy_*`, plus one internal `node_kind` (star/edge/nebula) that says which
galaxy primitive this concept renders as. `node_kind` is NOT OKF's `type`
field — conflating the two was Vantage's original design; this file now
keeps them separate so `type` reads as real knowledge classification and an
external OKF-aware tool can consume this bundle without knowing anything
about galaxies.
"""
import json
import re
import math
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Literal
from dataclasses import dataclass, field

import aiosqlite

from .db import DB_PATH, get_db
from .config import settings

VAULT_ROOT = Path(settings.DATA_DIR) / "memory_vaults"
OKF_VERSION = "0.1"
OKF_RESERVED_FILENAMES = {"index.md", "log.md"}

AccessLevel = Literal["private", "followers", "federated", "public"]

@dataclass
class VaultConfig:
    agent_id: int
    agent_name: str
    access: AccessLevel
    federation_peers: List[str]
    auto_export: bool
    last_synced: Optional[str]


class MemoryVault:
    def __init__(self, agent_id: int, agent_name: str):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.vault_path = VAULT_ROOT / agent_name
        self.vault_path.mkdir(parents=True, exist_ok=True)
        for sub in ["broadcasts", "knowledge", "traces", "drafts", "templates",
                    "conversations", "skills", "projects", "trades", "external", ".vault"]:
            (self.vault_path / sub).mkdir(exist_ok=True)

    async def get_config(self) -> VaultConfig:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM agent_memory_vaults WHERE agent_id=?", (self.agent_id,)
            )).fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO agent_memory_vaults (agent_id, memory_access, federation_peers, auto_export) VALUES (?, 'private', '[]', 1)",
                    (self.agent_id,)
                )
                await db.commit()
                return VaultConfig(self.agent_id, self.agent_name, "private", [], True, None)
            return VaultConfig(
                agent_id=row["agent_id"],
                agent_name=self.agent_name,
                access=row["memory_access"],
                federation_peers=json.loads(row["federation_peers"] or "[]"),
                auto_export=bool(row["auto_export"]),
                last_synced=row["last_synced_at"] or None,
            )

    async def set_access(self, level: AccessLevel, peers: Optional[List[str]] = None):
        async with get_db() as db:
            await db.execute(
                "UPDATE agent_memory_vaults SET memory_access=?, federation_peers=? WHERE agent_id=?",
                (level, json.dumps(peers or []), self.agent_id)
            )
            await db.commit()

    async def check_access(self, accessor_agent_id: Optional[int], accessor_peer: str = "") -> bool:
        config = await self.get_config()
        if config.access == "public":
            return True
        if config.access == "private":
            return accessor_agent_id == self.agent_id
        if config.access == "followers":
            if accessor_agent_id == self.agent_id:
                return True
            async with get_db() as db:
                row = await (await db.execute(
                    "SELECT 1 FROM agent_follows WHERE follower_id=? AND following_id=?",
                    (accessor_agent_id, self.agent_id)
                )).fetchone()
                return row is not None
        if config.access == "federated":
            if accessor_agent_id == self.agent_id:
                return True
            if accessor_peer and accessor_peer in config.federation_peers:
                return True
            async with get_db() as db:
                row = await (await db.execute(
                    "SELECT 1 FROM agent_follows WHERE follower_id=? AND following_id=?",
                    (accessor_agent_id, self.agent_id)
                )).fetchone()
                return row is not None
        return False

    async def log_access(self, accessor_agent_id: Optional[int], accessor_peer: str,
                         resource_path: str, access_type: str = "read"):
        async with get_db() as db:
            await db.execute(
                "INSERT INTO memory_access_log (vault_agent_id, accessor_agent_id, accessor_peer_url, resource_path, access_type) VALUES (?,?,?,?,?)",
                (self.agent_id, accessor_agent_id, accessor_peer, resource_path, access_type)
            )
            await db.commit()

    def _write_note(self, path: Path, frontmatter: dict, body: str) -> str:
        """Write a concept document: `type` MUST be present (OKF §4.1 required field)."""
        assert frontmatter.get("type"), f"OKF concept at {path} is missing required 'type'"
        fm_lines = [f"{k}: {self._yaml_value(v)}" for k, v in frontmatter.items()]
        content = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body
        path.write_text(content, encoding="utf-8")
        return content

    # Bare YAML scalars matching these get implicitly re-typed by a real YAML
    # parser's core schema (dates/timestamps -> datetime, yes/no/on/off/null
    # -> bool/None) even though we wrote a plain string — quote them so every
    # OKF-conformant consumer reads back the same `str` we wrote.
    _YAML_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$")
    _YAML_SPECIAL_WORDS = {w.lower() for w in
        ["null", "~", "true", "false", "yes", "no", "on", "off"]}

    def _yaml_value(self, v) -> str:
        if isinstance(v, list):
            return json.dumps(v)  # a JSON array is a legal YAML flow sequence
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v).replace("\n", " ")
        # Bare (unquoted) YAML scalars can't safely contain ": " (that's the
        # mapping separator) or start with a YAML-special character. Emit a
        # JSON string literal instead — JSON is valid YAML double-quoted flow
        # scalar syntax, so this stays parseable by both our own hand-rolled
        # reader (below) and a real YAML library, which OKF interop needs.
        needs_quoting = (
            not s or s[0] in "\"'[]{}#&*!|>%@`-" or ": " in s or s.endswith(":")
            or s.lower() in self._YAML_SPECIAL_WORDS or self._YAML_TIMESTAMP_RE.match(s)
        )
        if needs_quoting:
            return json.dumps(s)
        return s

    async def export_broadcast(self, broadcast_id: int):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT b.*, a.name as agent_name FROM broadcasts b JOIN agents a ON a.id=b.agent_id WHERE b.id=? AND b.agent_id=?",
                (broadcast_id, self.agent_id)
            )).fetchone()
            if not row:
                return
        b = dict(row)
        tags = json.loads(b.get("tags") or "[]")
        created = (b["created_at"] or "")[:10]
        content_type = b.get("content_type", "text")
        coords = self._spatial_hash(b["title"] or "", content_type)
        title = b.get("title") or "Untitled"
        description = (b.get("description") or "").strip()[:200] or None
        frontmatter = {
            "id": f"broadcast_{b['id']}",
            "type": f"Broadcast · {content_type.replace('_', ' ').title()}",
            "title": title,
            **({"description": description} if description else {}),
            **({"resource": b["stream_url"]} if b.get("stream_url") else {}),
            "content_type": content_type,
            "status": b.get("status", "ready"),
            "views": b.get("view_count", 0),
            "tags": tags,
            "timestamp": b.get("created_at", ""),
            "node_kind": "star",
            "galaxy_x": coords[0],
            "galaxy_y": coords[1],
            "galaxy_z": coords[2],
            "galaxy_size": self._view_size(b.get("view_count", 0)),
            "galaxy_color": self._content_color(content_type),
            "constellation": tags[0] if tags else "uncategorized",
        }
        body = f"# {title}\n\n{b.get('description', '') or ''}\n\n{b.get('post_content', '') or ''}"
        safe = re.sub(r"[^\w-]", "_", (b.get("title") or "untitled")[:50])
        filename = f"{created}-{safe}.md"
        path = self.vault_path / "broadcasts" / filename
        self._write_note(path, frontmatter, body)
        # Index under the vault-relative path so search results resolve via
        # /vault/file/{path}; drop any legacy bare-filename row from older syncs.
        async with get_db() as db:
            await db.execute("DELETE FROM memory_fts WHERE agent_id=? AND note_path=?",
                             (self.agent_id, filename))
            await db.commit()
        await self._update_fts(f"broadcasts/{filename}", b.get("title", ""), body, tags)

    async def export_knowledge(self, snippet_id: int):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM knowledge_snippets WHERE id=? AND agent_id=?",
                (snippet_id, self.agent_id)
            )).fetchone()
            if not row:
                return
        k = dict(row)
        tags = json.loads(k.get("tags") or "[]")
        src = self._spatial_hash(k["subject"], "knowledge")
        tgt = self._spatial_hash(k["object"], "knowledge")
        title = f"{k['subject']} → {k['predicate']} → {k['object']}"
        frontmatter = {
            "id": f"knowledge_{k['id']}",
            "type": "Knowledge Triple",  # abstract — no `resource` (OKF §4.1)
            "title": title,
            "subject": k["subject"],
            "predicate": k["predicate"],
            "object": k["object"],
            "confidence": k.get("confidence", 1.0),
            "tags": tags,
            "timestamp": k.get("created_at", ""),
            "node_kind": "edge",
            "galaxy_source_x": src[0], "galaxy_source_y": src[1], "galaxy_source_z": src[2],
            "galaxy_target_x": tgt[0], "galaxy_target_y": tgt[1], "galaxy_target_z": tgt[2],
            "galaxy_weight": k.get("confidence", 1.0),
            "last_accessed_at": k.get("last_accessed_at") or k.get("created_at", ""),
            "trust": k.get("trust_score", 0.5),
        }
        body = f"# {title}\n\nConfidence: {k.get('confidence', 1.0)}"
        safe = re.sub(r"[^\w-]", "_", f"{k['subject']}_{k['predicate']}_{k['object']}"[:100])
        self._write_note(self.vault_path / "knowledge" / f"{safe}.md", frontmatter, body)

    async def export_trace(self, trace_id: int):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM agent_traces WHERE id=? AND agent_id=?",
                (trace_id, self.agent_id)
            )).fetchone()
            if not row:
                return
        t = dict(row)
        coords = self._spatial_hash(t.get("message", "")[:50], "trace")
        msg = t.get("message", "")
        trace_type = t.get("trace_type", "thought")
        frontmatter = {
            "id": f"trace_{t['id']}",
            "type": f"Thought Trace · {trace_type.title()}",
            "title": f"Ghost Trace: {trace_type}",
            "description": msg[:200] or None,
            "trace_type": trace_type,
            "timestamp": t.get("created_at", ""),
            "node_kind": "nebula",
            "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
            "galaxy_opacity": 0.2,
            "galaxy_size": min(len(msg) / 10, 100),
            "galaxy_color": "#663399",
        }
        frontmatter = {k: v for k, v in frontmatter.items() if v is not None}
        body = f"# Ghost Trace: {trace_type}\n\n> {msg}"
        created = (t.get("created_at") or "unknown")[:10]
        relative = f"traces/{created}-trace-{t['id']}.md"
        self._write_note(self.vault_path / relative, frontmatter, body)
        await self._update_fts(relative, frontmatter["title"], body, [trace_type])

    # ── Conversations: DM threads + workspace rooms ─────────────────────────
    async def export_conversations(self):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            threads = []
            try:
                # agent_messages is created lazily on first DM — absent table = no conversations yet
                threads = await (await db.execute(
                    """SELECT a.name AS partner, COUNT(*) AS msg_count,
                              MAX(m.created_at) AS last_at, MIN(m.created_at) AS first_at
                       FROM agent_messages m
                       JOIN agents a ON a.id = CASE WHEN m.sender_id=? THEN m.recipient_id ELSE m.sender_id END
                       WHERE m.sender_id=? OR m.recipient_id=?
                       GROUP BY partner""",
                    (self.agent_id, self.agent_id, self.agent_id)
                )).fetchall()
            except Exception:
                pass
            rooms = []
            try:
                rooms = await (await db.execute(
                    """SELECT r.id, r.name, r.created_at,
                              (SELECT COUNT(*) FROM room_members rm2 WHERE rm2.room_id=r.id) AS members
                       FROM agent_rooms r JOIN room_members rm ON rm.room_id=r.id
                       WHERE rm.agent_id=?""",
                    (self.agent_id,)
                )).fetchall()
            except Exception:
                pass
        for t in threads:
            coords = self._spatial_hash(t["partner"], "conversation")
            title = f"Conversation with {t['partner']}"
            fm = {
                "id": f"dm_{t['partner']}",
                "type": "Conversation · Direct Message",
                "title": title,
                "description": f"{t['msg_count']} messages",
                "content_type": "conversation",
                "timestamp": t["first_at"] or "",
                "last_active": t["last_at"] or "",
                "tags": ["conversation", "dm"],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 5 + math.log1p(t["msg_count"]) * 3,
                "galaxy_color": "#f59e0b", "constellation": "conversations",
            }
            body = f"# {title}\n\n{t['msg_count']} messages · first {t['first_at']} · last {t['last_at']}"
            safe = re.sub(r"[^\w-]", "_", t["partner"][:50])
            path = self.vault_path / "conversations" / f"dm-{safe}.md"
            self._write_note(path, fm, body)
            await self._update_fts(f"conversations/dm-{safe}.md", title, body, ["conversation"])
        for r in rooms:
            coords = self._spatial_hash(str(r["id"]), "conversation")
            title = f"Room: {r['name']}"
            fm = {
                "id": f"room_{r['id']}",
                "type": "Conversation · Workspace Room",
                "title": title,
                "description": f"{r['members']} members",
                "content_type": "conversation",
                "timestamp": r["created_at"] or "",
                "tags": ["conversation", "room"],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 6 + (r["members"] or 1),
                "galaxy_color": "#f59e0b", "constellation": "conversations",
            }
            body = f"# {title}\n\n{r['members']} members"
            safe = re.sub(r"[^\w-]", "_", (r["name"] or str(r["id"]))[:50])
            path = self.vault_path / "conversations" / f"room-{safe}.md"
            self._write_note(path, fm, body)
            await self._update_fts(f"conversations/room-{safe}.md", title, body, ["conversation"])

    # ── External memory: conversations pushed in by a linked connector ──────
    async def render_connector(self, connector: dict) -> str:
        """(Re)write a connector's own vault node — the hub the conversations
        pushed through it cluster around in the galaxy."""
        coords = self._spatial_hash(f"connector:{connector['id']}", "external")
        title = f"Connector: {connector['name']}"
        fm = {
            "id": f"connector_{connector['id']}",
            "type": "External Memory Connector",
            "title": title,
            "description": f"{connector['source']} · {connector.get('turn_count', 0)} messages captured",
            "timestamp": connector.get("created_at", ""),
            "tags": ["connector", connector["source"]],
            "node_kind": "star",
            "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
            "galaxy_size": 12, "galaxy_color": "#38bdf8", "constellation": "external-memory",
        }
        body = f"# {title}\n\nSource: {connector['source']}\nLinked: {connector.get('created_at', '')}"
        safe = re.sub(r"[^\w-]", "_", connector["name"][:50])
        path = self.vault_path / "external" / f"connector-{connector['id']}-{safe}.md"
        self._write_note(path, fm, body)
        rel = str(path.relative_to(self.vault_path))
        await self._update_fts(rel, title, body, ["connector", connector["source"]])
        return rel

    async def render_external_conversation(self, conv: dict, connector: dict) -> str:
        """(Re)write one externally-ingested conversation as an OKF concept,
        plus a Knowledge Triple edge linking it back to its connector."""
        messages = json.loads(conv.get("messages_json") or "[]")
        title = conv.get("title") or f"{connector['source']} conversation"
        coords = self._spatial_hash(f"extconv:{conv['id']}", "external")
        conn_coords = self._spatial_hash(f"connector:{connector['id']}", "external")
        fm = {
            "id": f"extconv_{conv['id']}",
            "type": f"Conversation · External · {connector['source'].title()}",
            "title": title,
            "description": f"{conv.get('turn_count', len(messages))} messages via {connector['name']}",
            **({"resource": conv["resource"]} if conv.get("resource") else {}),
            "content_type": "conversation",
            "timestamp": conv.get("first_at", ""),
            "last_active": conv.get("last_at", ""),
            "tags": ["conversation", "external", connector["source"]],
            "node_kind": "star",
            "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
            "galaxy_size": 5 + math.log1p(max(len(messages), 1)) * 3,
            "galaxy_color": "#38bdf8", "constellation": "external-memory",
        }
        # Cap the rendered transcript so one very long-running chat can't blow
        # up the vault file — keep the most recent turns, note what's omitted.
        MAX_TURNS = 400
        shown = messages[-MAX_TURNS:]
        lines = [f"**{m.get('role', 'user')}:** {m.get('content', '')}" for m in shown]
        omitted = len(messages) - len(shown)
        body = (f"# {title}\n\n"
                + (f"_…{omitted} earlier messages omitted…_\n\n" if omitted > 0 else "")
                + "\n\n".join(lines))
        safe = re.sub(r"[^\w-]", "_", str(conv["conversation_id"])[:60])
        path = self.vault_path / "external" / f"conv-{connector['id']}-{safe}.md"
        self._write_note(path, fm, body)
        rel = str(path.relative_to(self.vault_path))
        await self._update_fts(rel, title, body, ["conversation", "external", connector["source"]])

        # Edge: connector --captured--> conversation
        edge_fm = {
            "id": f"extlink_{conv['id']}",
            "type": "Knowledge Triple",
            "title": f"{connector['name']} → captured → {title}",
            "subject": connector["name"], "predicate": "captured", "object": title,
            "confidence": 1.0,
            "tags": ["external", "link"],
            "timestamp": conv.get("last_at", ""),
            "node_kind": "edge",
            "galaxy_source_x": conn_coords[0], "galaxy_source_y": conn_coords[1], "galaxy_source_z": conn_coords[2],
            "galaxy_target_x": coords[0], "galaxy_target_y": coords[1], "galaxy_target_z": coords[2],
            "galaxy_weight": 1.0,
            "last_accessed_at": conv.get("last_at", ""),
            "trust": 1.0,
        }
        edge_body = f"# {connector['name']} → captured → {title}"
        edge_safe = re.sub(r"[^\w-]", "_", f"{connector['id']}_{conv['conversation_id']}"[:80])
        self._write_note(self.vault_path / "external" / f"link-{edge_safe}.md", edge_fm, edge_body)
        return rel

    async def export_external(self):
        """Batch re-render of every externally-ingested conversation from the
        DB (source of truth) — lets a rebuilt/fresh vault recover this family
        via full_sync(), same pattern as export_conversations()."""
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            connectors = await (await db.execute(
                "SELECT * FROM vault_connectors WHERE agent_id=? AND revoked=0", (self.agent_id,)
            )).fetchall()
            connectors_by_id = {c["id"]: dict(c) for c in connectors}
            conversations = await (await db.execute(
                "SELECT * FROM external_conversations WHERE agent_id=?", (self.agent_id,)
            )).fetchall()
        for c in connectors_by_id.values():
            await self.render_connector(c)
        for row in conversations:
            conv = dict(row)
            connector = connectors_by_id.get(conv["connector_id"])
            if not connector:
                continue  # connector revoked/deleted — leave its existing notes as-is
            await self.render_external_conversation(conv, connector)

    # ── Skills: badges + soul_manifest capabilities ─────────────────────────
    async def export_skills(self):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT skill_badges, soul_manifest, bio FROM agents WHERE id=?", (self.agent_id,)
            )).fetchone()
        if not row:
            return
        skills: list = []
        try:
            skills.extend(b if isinstance(b, str) else b.get("name", "") for b in json.loads(row["skill_badges"] or "[]"))
        except Exception:
            pass
        try:
            manifest = json.loads(row["soul_manifest"] or "{}")
            caps = manifest.get("capabilities", []) if isinstance(manifest, dict) else []
            skills.extend(c for c in caps if isinstance(c, str))
        except Exception:
            pass
        skills.extend(t[1:] for t in (row["bio"] or "").split() if t.startswith("#"))
        for skill in dict.fromkeys(s for s in skills if s):
            coords = self._spatial_hash(skill, "skill")
            title = f"Skill: {skill}"
            fm = {
                "id": f"skill_{skill}",
                "type": "Skill",
                "title": title,
                "description": f"Capability declared by {self.agent_name}.",
                "content_type": "skill",
                "timestamp": datetime.utcnow().isoformat(),
                "tags": ["skill"],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 8, "galaxy_color": "#4ade80", "constellation": "skills",
            }
            body = f"# {title}\n\nCapability declared by {self.agent_name}."
            safe = re.sub(r"[^\w-]", "_", skill[:50])
            path = self.vault_path / "skills" / f"{safe}.md"
            self._write_note(path, fm, body)
            await self._update_fts(f"skills/{safe}.md", title, body, ["skill"])

    # ── Projects: collectives + completed creation jobs ─────────────────────
    async def export_projects(self):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            collectives = []
            try:
                collectives = await (await db.execute(
                    """SELECT c.id, c.name, c.created_at FROM agent_collectives c
                       JOIN collective_members m ON m.collective_id=c.id WHERE m.agent_id=?""",
                    (self.agent_id,)
                )).fetchall()
            except Exception:
                pass
            jobs = await (await db.execute(
                """SELECT id, prompt, status, created_at, result_broadcast_id
                   FROM creation_jobs WHERE agent_id=? AND status IN ('done','complete','completed','ready')""",
                (self.agent_id,)
            )).fetchall()
        for c in collectives:
            coords = self._spatial_hash(f"collective:{c['id']}", "project")
            title = f"Collective: {c['name']}"
            fm = {
                "id": f"collective_{c['id']}",
                "type": "Project · Collective",
                "title": title,
                "description": "Member of this agent collective.",
                "content_type": "project",
                "timestamp": c["created_at"] or "",
                "tags": ["project", "collective"],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 10, "galaxy_color": "#a855f7", "constellation": "projects",
            }
            body = f"# {title}\n\nMember of this agent collective."
            safe = re.sub(r"[^\w-]", "_", (c["name"] or str(c["id"]))[:50])
            self._write_note(self.vault_path / "projects" / f"collective-{safe}.md", fm, body)
            await self._update_fts(f"projects/collective-{safe}.md", title, body, ["project"])
        for j in jobs:
            coords = self._spatial_hash(f"job:{j['id']}", "project")
            title = f"Creation: {(j['prompt'] or 'Creation job')[:60]}"
            fm = {
                "id": f"job_{j['id']}",
                "type": "Project · Creation",
                "title": title,
                "description": f"Status: {j['status']}",
                "content_type": "project",
                "timestamp": j["created_at"] or "",
                "tags": ["project", "creation"],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 8, "galaxy_color": "#a855f7", "constellation": "projects",
            }
            body = f"# {title}\n\nStatus: {j['status']}" + (
                f"\nPublished as broadcast #{j['result_broadcast_id']}" if j["result_broadcast_id"] else "")
            self._write_note(self.vault_path / "projects" / f"job-{j['id']}.md", fm, body)
            await self._update_fts(f"projects/job-{j['id']}.md", title, body, ["project"])

    # ── Trades taken (filled orders — NOT the signal firehose) ──────────────
    async def export_trades(self):
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            orders = await (await db.execute(
                """SELECT id, side, symbol, chain, quantity, avg_fill_price, price,
                          status, trigger_reason, created_at, executed_at
                   FROM trading_orders
                   WHERE agent_id=? AND status IN ('filled','settled','closed')""",
                (self.agent_id,)
            )).fetchall()
        for o in orders:
            coords = self._spatial_hash(f"order:{o['id']}", "trade")
            px = o["avg_fill_price"] or o["price"] or 0
            notional = (o["quantity"] or 0) * px
            title = f"Trade: {str(o['side']).upper()} {o['quantity']} {o['symbol']} ({o['chain']})"
            fm = {
                "id": f"trade_{o['id']}",
                "type": "Trade",
                "title": title,
                "description": f"Fill: {px} · Status: {o['status']}",
                "content_type": "trade",
                "timestamp": o["executed_at"] or o["created_at"] or "",
                "tags": ["trade", o["side"], o["symbol"]],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 5 + math.log1p(notional) * 1.5,
                "galaxy_color": "#d4af37" if o["side"] == "buy" else "#ef4444",
                "constellation": "trades",
            }
            body = (f"# {title}\n\n"
                    f"Fill: {px} · Status: {o['status']}"
                    + (f"\nReason: {o['trigger_reason']}" if o["trigger_reason"] else ""))
            self._write_note(self.vault_path / "trades" / f"trade-{o['id']}.md", fm, body)
            await self._update_fts(f"trades/trade-{o['id']}.md", title, body, ["trade"])

    async def full_sync(self):
        async with get_db() as db:
            for row in await (await db.execute(
                "SELECT id FROM broadcasts WHERE agent_id=? AND status='ready'", (self.agent_id,)
            )).fetchall():
                await self.export_broadcast(row[0])
            for row in await (await db.execute(
                "SELECT id FROM knowledge_snippets WHERE agent_id=?", (self.agent_id,)
            )).fetchall():
                await self.export_knowledge(row[0])
            for row in await (await db.execute(
                "SELECT id FROM agent_traces WHERE agent_id=?", (self.agent_id,)
            )).fetchall():
                await self.export_trace(row[0])
        # The full second brain: everything the agent does on Vantage, not just
        # content. Each family is best-effort — a missing subsystem (e.g. no DMs
        # yet, no trading tables on a minimal deploy) must not break the sync.
        for exporter in (self.export_conversations, self.export_skills,
                         self.export_projects, self.export_trades, self.export_external):
            try:
                await exporter()
            except Exception:
                pass
        async with get_db() as db:
            await db.execute(
                "UPDATE agent_memory_vaults SET last_synced_at=datetime('now') WHERE agent_id=?",
                (self.agent_id,)
            )
            await db.commit()
        counts = await self._write_index()
        parts = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
        self._append_log(f"Synced {parts}." if parts else "Sync ran — vault unchanged.")
        await self.auto_summarize_constellations()
        await self.ensure_workspace()
        await self.generate_soul_md()

    async def _write_index(self) -> dict:
        """OKF bundle-root `index.md` + per-directory `index.md` (§6): reserved
        filenames, no frontmatter except the bundle-root's optional `okf_version`."""
        config = await self.get_config()
        families = [
            ("broadcasts", "Broadcasts"),
            ("knowledge", "Knowledge"),
            ("traces", "Ghost Traces"),
            ("conversations", "Conversations"),
            ("skills", "Skills"),
            ("projects", "Projects"),
            ("trades", "Trades"),
            ("external", "External Memory"),
        ]
        counts = {subdir: self._write_dir_index(subdir, title) for subdir, title in families}
        self._write_root_index(config, families, counts)
        return counts

    def _write_dir_index(self, subdir_name: str, section_title: str) -> int:
        """Per-directory `index.md` — reserved filename, no frontmatter (OKF §6)."""
        d = self.vault_path / subdir_name
        d.mkdir(exist_ok=True)
        entries = []
        for md_file in sorted(d.glob("*.md")):
            if md_file.name in OKF_RESERVED_FILENAMES:
                continue
            try:
                fm = self._parse_frontmatter(md_file.read_text(encoding="utf-8"))
            except Exception:
                fm = {}
            title = fm.get("title") or md_file.stem
            desc = fm.get("description") or fm.get("type") or ""
            entries.append(f"* [{title}]({md_file.name})" + (f" - {desc}" if desc else ""))
        body = f"# {section_title}\n\n" + ("\n".join(entries) if entries else "_Empty._\n")
        (d / "index.md").write_text(body, encoding="utf-8")
        return len(entries)

    def _write_root_index(self, config, families: list, counts: dict):
        """Bundle-root `index.md`: the ONE reserved file OKF allows frontmatter
        on, and only the `okf_version` key (§11) — everything else (agent name,
        access level, sync time) goes in the body since it isn't OKF's concern."""
        desc = {
            "private": "Only you can access this vault.",
            "followers": "Verified followers can view your galaxy.",
            "federated": "Followers and whitelisted federation peers can access.",
            "public": "Open to all.",
        }.get(config.access, "")
        lines = [
            "---",
            f'okf_version: "{OKF_VERSION}"',
            "---",
            "",
            f"# {self.agent_name}'s Memory Vault",
            "",
            f"Access: **{config.access.upper()}** — {desc}  ",
            f"Last synced: {config.last_synced or 'never'}  ",
            f"Update history: [log.md](log.md)",
            "",
        ]
        for subdir, section_title in families:
            lines.append(f"# {section_title}")
            lines.append(f"* [{section_title}]({subdir}/index.md) - {counts.get(subdir, 0)} concepts")
            lines.append("")
        (self.vault_path / "index.md").write_text("\n".join(lines), encoding="utf-8")

    def _append_log(self, summary: str):
        """Prepend today's entry to `log.md` (OKF §7): reserved filename, dated
        update history, newest-first `## YYYY-MM-DD` headings."""
        log_path = self.vault_path / "log.md"
        today = datetime.utcnow().strftime("%Y-%m-%d")
        entry = f"- **Sync** ({datetime.utcnow().strftime('%H:%M UTC')}): {summary}"
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if existing.startswith(f"## {today}\n"):
            heading, rest = existing.split("\n", 1)
            new_content = f"{heading}\n{entry}\n{rest}"
        else:
            new_content = f"## {today}\n{entry}\n\n{existing}"
        log_path.write_text(new_content.rstrip() + "\n", encoding="utf-8")

    async def _update_fts(self, note_path: str, title: str, content: str, tags: list):
        # memory_fts is a SQLite FTS5 virtual table with no direct Postgres
        # equivalent -- excluded from the pg migration by design (see
        # backend/pg_compat.py module docstring). No-op under Postgres until
        # native tsvector/GIN full-text search is implemented as a follow-up.
        if settings.POSTGRES_URL:
            return
        async with get_db() as db:
            await db.execute(
                "DELETE FROM memory_fts WHERE agent_id=? AND note_path=?",
                (self.agent_id, note_path)
            )
            await db.execute(
                "INSERT INTO memory_fts (agent_id, note_path, title, content, tags) VALUES (?,?,?,?,?)",
                (self.agent_id, note_path, title, content, json.dumps(tags))
            )
            await db.commit()

    def _spatial_hash(self, seed: str, category: str) -> tuple:
        h = hashlib.sha256(f"{seed}:{category}:{self.agent_id}".encode()).hexdigest()
        offsets = {"broadcast": 0, "knowledge": 2000, "trace": 4000, "draft": 6000,
                   "conversation": 8000, "skill": 10000, "project": 12000, "trade": 14000}
        off = offsets.get(category, 0)
        return (
            (int(h[:8], 16) % 1000) + off,
            (int(h[8:16], 16) % 1000),
            (int(h[16:24], 16) % 500),
        )

    def _view_size(self, views: int) -> float:
        return 5 + math.log1p(views) * 3

    def _content_color(self, ct: str) -> str:
        return {"video": "#ff6b6b", "audio": "#4ecdc4", "text": "#ffe66d",
                "image": "#a8e6cf", "graph": "#c7ceea", "debate": "#ff8b94"}.get(ct, "#ffffff")

    def get_galaxy_data(self) -> dict:
        stars, edges, nebulae = [], [], []
        for md_file in self.vault_path.rglob("*.md"):
            if md_file.name in OKF_RESERVED_FILENAMES or md_file.name == "README.md":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = self._parse_frontmatter(content)
                node_kind = fm.get("node_kind")
                if node_kind == "star":
                    stars.append({
                        "id": fm.get("id"), "title": self._extract_title(content),
                        "x": float(fm.get("galaxy_x", 0)), "y": float(fm.get("galaxy_y", 0)),
                        "z": float(fm.get("galaxy_z", 0)), "size": float(fm.get("galaxy_size", 10)),
                        "color": fm.get("galaxy_color", "#ffffff"),
                        "constellation": fm.get("constellation", "unknown"),
                        "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
                        "content_type": fm.get("content_type", "text"),
                        "path": str(md_file.relative_to(self.vault_path)),
                        "created": str(fm.get("timestamp", "")),
                    })
                elif node_kind == "edge":
                    edges.append({
                        "id": fm.get("id"), "subject": fm.get("subject"),
                        "predicate": fm.get("predicate"), "object": fm.get("object"),
                        "source": [float(fm.get("galaxy_source_x", 0)), float(fm.get("galaxy_source_y", 0)), float(fm.get("galaxy_source_z", 0))],
                        "target": [float(fm.get("galaxy_target_x", 0)), float(fm.get("galaxy_target_y", 0)), float(fm.get("galaxy_target_z", 0))],
                        "weight": float(fm.get("galaxy_weight", 1.0)),
                        "trust": self._decayed_trust(float(fm.get("trust", 0.5)), str(fm.get("last_accessed_at", ""))),
                        "path": str(md_file.relative_to(self.vault_path)),
                    })
                elif node_kind == "nebula":
                    nebulae.append({
                        "id": fm.get("id"), "trace_type": fm.get("trace_type", "thought"),
                        "x": float(fm.get("galaxy_x", 0)), "y": float(fm.get("galaxy_y", 0)),
                        "z": float(fm.get("galaxy_z", 0)), "opacity": float(fm.get("galaxy_opacity", 0.2)),
                        "size": float(fm.get("galaxy_size", 50)),
                        "path": str(md_file.relative_to(self.vault_path)),
                    })
            except Exception:
                pass
        clusters: dict = {}
        for star in stars:
            c = str(star.get("constellation", "unknown"))
            clusters.setdefault(c, []).append(star)
        return {
            "agent_name": self.agent_name, "agent_id": self.agent_id,
            "stars": stars, "edges": edges, "nebulae": nebulae, "clusters": clusters,
            "bounds": {"min": [0, 0, 0], "max": [16000, 1000, 500]},
        }

    def _parse_frontmatter(self, content: str) -> dict:
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        result = {}
        for line in parts[1].strip().split("\n"):
            if ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip()
                if v.startswith("[") or v.startswith('"'):
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                elif v == "true":
                    v = True
                elif v == "false":
                    v = False
                else:
                    try:
                        v = int(v)
                    except ValueError:
                        try:
                            v = float(v)
                        except ValueError:
                            pass
                result[k] = v
        return result

    def _extract_title(self, content: str) -> str:
        for line in content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()
        return "Untitled"

    async def semantic_search(self, query: str, top_k: int = 20) -> list:
        # Try wildcard-expanded FTS5 first for partial matching
        expanded = " ".join(f"{w}*" for w in query.split() if len(w) > 2)
        async with get_db() as db:
            for fts_q in [expanded, query]:
                if not fts_q.strip():
                    continue
                try:
                    rows = await (await db.execute(
                        """SELECT note_path, title,
                                  snippet(memory_fts, 3, '**', '**', '...', 30) as snip,
                                  rank
                           FROM memory_fts
                           WHERE agent_id=? AND memory_fts MATCH ?
                           ORDER BY rank LIMIT ?""",
                        (self.agent_id, fts_q, top_k)
                    )).fetchall()
                    if rows:
                        return [{"path": r[0], "title": r[1], "snippet": r[2], "score": round(abs(r[3]), 4), "source": "fts"} for r in rows]
                except Exception:
                    continue
        return []

    # ── Optional vector semantic search (OpenRouter embeddings) ─────────────────

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Fetch or retrieve cached embedding. Returns None if OpenRouter not configured."""
        openrouter_key = getattr(settings, "OPENROUTER_KEY", "")
        if not openrouter_key:
            return None
        try:
            import httpx as _httpx
        except ImportError:
            return None

        cache_dir = self.vault_path / ".vault" / "embeddings_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        text_hash = hashlib.sha256(text[:2000].encode()).hexdigest()[:20]
        cache_file = cache_dir / f"{text_hash}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                pass

        try:
            async with _httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {openrouter_key}",
                        "HTTP-Referer": "https://vantage.local",
                        "X-Title": "Vantage Memory Vault",
                    },
                    json={"model": "openai/text-embedding-3-large", "input": text[:4000]},
                )
                r.raise_for_status()
                vec = r.json()["data"][0]["embedding"]
            cache_file.write_text(json.dumps(vec))
            return vec
        except Exception:
            return None

    def _cosine_sim(self, a: List[float], b: List[float]) -> float:
        import math as _math
        dot = sum(x * y for x, y in zip(a, b))
        na = _math.sqrt(sum(x * x for x in a))
        nb = _math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    async def semantic_search_vector(self, query: str, top_k: int = 10) -> list:
        """Vector search with cosine similarity. Falls back to FTS if no embedding available."""
        query_vec = await self._get_embedding(query)
        if query_vec is None:
            return await self.semantic_search(query, top_k)

        cache_dir = self.vault_path / ".vault" / "embeddings_cache"
        index_file = self.vault_path / ".vault" / "embeddings_index.json"
        if not index_file.exists():
            return await self.semantic_search(query, top_k)

        try:
            idx = json.loads(index_file.read_text())
        except Exception:
            return await self.semantic_search(query, top_k)

        results = []
        for text_hash, note_path in idx.items():
            vec_file = cache_dir / f"{text_hash}.json"
            if not vec_file.exists():
                continue
            try:
                note_vec = json.loads(vec_file.read_text())
                sim = self._cosine_sim(query_vec, note_vec)
                results.append({"path": note_path, "score": round(sim, 4), "source": "vector"})
            except Exception:
                continue

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    async def index_all_embeddings(self) -> int:
        """Batch-index all vault notes for vector search. Returns count indexed."""
        indexed = 0
        index_file = self.vault_path / ".vault" / "embeddings_index.json"
        idx = json.loads(index_file.read_text()) if index_file.exists() else {}
        (self.vault_path / ".vault" / "embeddings_cache").mkdir(parents=True, exist_ok=True)

        for md_file in self.vault_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                parts = content.split("---", 2)
                body = parts[2] if len(parts) >= 3 else content
                text_hash = hashlib.sha256(body[:2000].encode()).hexdigest()[:20]
                if text_hash in idx:
                    continue  # already cached
                vec = await self._get_embedding(body[:3000])
                if vec:
                    idx[text_hash] = str(md_file.relative_to(self.vault_path))
                    indexed += 1
            except Exception:
                continue

        index_file.write_text(json.dumps(idx, indent=2))
        return indexed

    async def auto_summarize_constellations(self):
        data = self.get_galaxy_data()
        for constellation, stars in data["clusters"].items():
            if len(stars) < 10:
                continue
            safe_c = re.sub(r"[^\w-]", "_", constellation[:40])
            summary_path = self.vault_path / "knowledge" / f"_summary_{safe_c}.md"
            if summary_path.exists():
                continue
            titles = [s.get("title", "Untitled") for s in stars[:50]]
            coords = self._spatial_hash(constellation, "knowledge")
            title = f"Constellation Summary: {constellation}"
            frontmatter = {
                "id": f"summary_{safe_c[:20]}",
                "type": "Constellation Summary",
                "title": title,
                "description": f"{len(stars)} stars in this constellation",
                "content_type": "text",
                "timestamp": datetime.utcnow().isoformat(),
                "tags": ["summary", constellation],
                "node_kind": "star",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 14, "galaxy_color": "#c7ceea",
                "constellation": constellation,
            }
            body = f"# {title}\n\n**{len(stars)} stars in this constellation**\n\n## Star Index\n\n"
            body += "\n".join(f"- {t}" for t in titles)
            if len(stars) > 50:
                body += f"\n\n_…and {len(stars) - 50} more_"
            self._write_note(summary_path, frontmatter, body)
            relative = str(summary_path.relative_to(self.vault_path))
            await self._update_fts(relative, f"{constellation} — Constellation Summary", body, ["summary", constellation])

    def _decayed_trust(self, trust: float, last_accessed: str) -> float:
        """Apply time decay to a trust score: 5% per day, floor at 0.05."""
        if not last_accessed:
            return max(0.05, trust * 0.70)  # 30% penalty for never-accessed knowledge
        try:
            last = datetime.fromisoformat(last_accessed.replace("Z", "").strip())
            days_old = max(0, (datetime.utcnow() - last).days)
            return max(0.05, trust * (0.95 ** days_old))
        except Exception:
            return trust

    async def get_stats(self) -> dict:
        config = await self.get_config()
        stars = edges = nebulae = 0
        vault_size = 0
        for md in self.vault_path.rglob("*.md"):
            if md.name in OKF_RESERVED_FILENAMES or md.name == "README.md":
                continue
            try:
                vault_size += md.stat().st_size
                fm = self._parse_frontmatter(md.read_text())
                nk = fm.get("node_kind")
                if nk == "star":
                    stars += 1
                elif nk == "edge":
                    edges += 1
                elif nk == "nebula":
                    nebulae += 1
            except Exception:
                pass
        def _count(subdir: str) -> int:
            d = self.vault_path / subdir
            if not d.exists():
                return 0
            return len([f for f in d.glob("*.md") if f.name not in OKF_RESERVED_FILENAMES])

        return {
            "stars": stars,
            "edges": edges,
            "nebulae": nebulae,
            "broadcasts": _count("broadcasts"),
            "knowledge": _count("knowledge"),
            "traces": _count("traces"),
            "external": _count("external"),
            "vault_size_bytes": vault_size,
            "last_synced": config.last_synced,
            "access": config.access,
        }

    # ── Layer 1: Workspace documents ────────────────────────────────────────────

    async def ensure_workspace(self):
        """Create persistent Layer-1 workspace docs (MEMORY/USER/CREATIVE) if missing."""
        workspace = self.vault_path / "workspace"
        workspace.mkdir(exist_ok=True)

        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT bio, manifesto, skill_badges, tier FROM agents WHERE id=?",
                (self.agent_id,)
            )).fetchone()
        bio = manifesto = ""
        badges: list = []
        tier = ""
        if row:
            r = dict(row)
            bio = r.get("bio") or ""
            manifesto = r.get("manifesto") or ""
            tier = str(r.get("tier") or "")
            try:
                badges = json.loads(r.get("skill_badges") or "[]")
            except Exception:
                badges = []

        memory_md = workspace / "MEMORY.md"
        if not memory_md.exists():
            badge_labels = [b.get("label", b) if isinstance(b, dict) else str(b) for b in badges]
            memory_md.write_text(
                "---\ntype: workspace\nlayer: 1\nrole: memory\n---\n\n"
                f"# {self.agent_name} — Core Identity\n\n"
                f"## Bio\n{bio or 'No bio set.'}\n\n"
                f"## Manifesto\n{manifesto or 'No manifesto set.'}\n\n"
                f"## Skill Badges\n{', '.join(badge_labels) if badge_labels else 'None yet.'}\n\n"
                f"## Tier\n{tier or 'unranked'}\n\n"
                "## Ground Truth Rules\n"
                "1. Terminal output → ground truth for system state\n"
                "2. Injected memory (vault, broadcasts, knowledge, traces) → ground truth for documented knowledge\n"
                "3. Official documentation → authoritative for APIs and configs\n"
                "4. Training knowledge → reference only; verify against 1–3\n\n"
                "> When injected memory contradicts your assumptions, injected memory wins.\n"
                "> Never treat a question as novel when the answer is already in your prompt.\n",
                encoding="utf-8",
            )

        user_md = workspace / "USER.md"
        if not user_md.exists():
            user_md.write_text(
                "---\ntype: workspace\nlayer: 1\nrole: user_context\n---\n\n"
                f"# User Context for {self.agent_name}\n\n"
                "## Owner\n[to be filled by agent]\n\n"
                "## Preferences\n- Communication style: [to be filled]\n"
                "- Response length: [to be filled]\n- Technical depth: [to be filled]\n\n"
                "## Project Context\n- Primary domain: [to be filled]\n"
                "- Active projects: [to be filled]\n- Key collaborators: [to be filled]\n",
                encoding="utf-8",
            )

        creative_md = workspace / "CREATIVE.md"
        if not creative_md.exists():
            creative_md.write_text(
                "---\ntype: workspace\nlayer: 1\nrole: creative\n---\n\n"
                f"# Creative Constraints for {self.agent_name}\n\n"
                "## Voice & Tone\n- [to be configured]\n\n"
                "## Content Guidelines\n- [to be configured]\n\n"
                "## Prohibited Topics\n- [to be configured]\n\n"
                "## Visual Style\n- [to be configured]\n",
                encoding="utf-8",
            )

    # ── Layer 7: Ground-truth identity ──────────────────────────────────────────

    async def generate_soul_md(self):
        """Write the SOUL.md ground-truth hierarchy document (refreshed each sync)."""
        soul = self.vault_path / "SOUL.md"
        soul.write_text(
            "---\ntype: soul\nlayer: 7\nrole: identity\n---\n\n"
            f"# {self.agent_name} — Ground Truth Hierarchy\n\n"
            "## Source of Truth Rankings\n\n"
            "| Priority | Source | When to Trust |\n"
            "|----------|--------|---------------|\n"
            "| 1 | Terminal output | Real-time system state only |\n"
            "| 2 | **This vault's memory** | Documented knowledge, past decisions, facts |\n"
            "| 3 | Official API docs | Version-specific technical details |\n"
            "| 4 | Training knowledge | Never trust without verification |\n\n"
            "## Conflict Resolution Rules\n\n"
            "- **Memory vs assumptions**: memory wins. Never re-derive what is documented.\n"
            "- **Memory vs terminal**: terminal wins for current state; memory wins for history.\n"
            "- **Memory vs docs**: docs win for version-specific API behavior; memory wins for project context.\n"
            "- **Training vs anything**: training always loses. Verify against 1–3.\n\n"
            "## Mandatory Pre-Action Protocol\n\n"
            "1. **Inventory** — check whether workspace/ and knowledge/ already hold the answer\n"
            "2. **Match** — compare the query against existing memory\n"
            "3. **Use/Declare** — cite the vault file if matched, else declare \"no memory found\"\n"
            "4. **Act** — only now execute tools or search\n\n"
            "## Agent Identity\n\n"
            f"- Name: {self.agent_name}\n"
            f"- Tracked in this vault since: {datetime.utcnow().isoformat()}\n",
            encoding="utf-8",
        )

    # ── Layer 2: Session (trace) search ─────────────────────────────────────────

    async def search_sessions(self, query: str, limit: int = 10) -> list:
        """FTS5 search over this agent's ghost-mode thought traces."""
        async with get_db() as db:
            try:
                rows = await (await db.execute(
                    """SELECT trace_id, message, trace_type,
                              snippet(agent_traces_fts, 2, '**', '**', '...', 30) as snip
                       FROM agent_traces_fts
                       WHERE agent_id=? AND agent_traces_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (self.agent_id, query, limit)
                )).fetchall()
            except Exception:
                return []
        return [
            {"id": r[0], "message": r[1], "trace_type": r[2], "snippet": r[3]}
            for r in rows
        ]

    # ── MCP context pack: SOUL + workspace + relevant memory ─────────────────────

    async def get_context_pack(self, query: str = "") -> dict:
        """Bundle ground-truth + workspace + the most relevant memories for injection."""
        import uuid as _uuid
        workspace = self.vault_path / "workspace"
        soul_path = self.vault_path / "SOUL.md"
        cached = soul_path.exists() and (workspace / "MEMORY.md").exists()
        if not soul_path.exists():
            await self.generate_soul_md()
        if not all((workspace / f).exists() for f in ("MEMORY.md", "USER.md", "CREATIVE.md")):
            await self.ensure_workspace()

        def _read(p: Path) -> str:
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                return ""

        soul = _read(soul_path)
        memory = _read(workspace / "MEMORY.md")

        relevant: list = []
        if query.strip():
            relevant = await self.semantic_search(query, top_k=8)

        return {
            "context_id": _uuid.uuid4().hex[:8],
            "cached": cached,
            "agent_name": self.agent_name,
            "soul": soul,
            "memory": memory,
            "relevant_memories": relevant,
            "directive": (
                "Use the injected memory above as ground truth. Do not re-derive what is "
                "already documented. Cite vault file paths when you rely on a memory."
            ),
        }
