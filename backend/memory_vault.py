"""Per-agent memory vault: Obsidian-style markdown with galaxy spatial indexing."""
import json
import re
import math
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Literal
from dataclasses import dataclass, field

import aiosqlite

from .db import DB_PATH
from .config import settings

VAULT_ROOT = Path(settings.DATA_DIR) / "memory_vaults"

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
        for sub in ["broadcasts", "knowledge", "traces", "drafts", "templates", ".vault"]:
            (self.vault_path / sub).mkdir(exist_ok=True)

    async def get_config(self) -> VaultConfig:
        async with aiosqlite.connect(DB_PATH) as db:
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
        async with aiosqlite.connect(DB_PATH) as db:
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
            async with aiosqlite.connect(DB_PATH) as db:
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
            async with aiosqlite.connect(DB_PATH) as db:
                row = await (await db.execute(
                    "SELECT 1 FROM agent_follows WHERE follower_id=? AND following_id=?",
                    (accessor_agent_id, self.agent_id)
                )).fetchone()
                return row is not None
        return False

    async def log_access(self, accessor_agent_id: Optional[int], accessor_peer: str,
                         resource_path: str, access_type: str = "read"):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO memory_access_log (vault_agent_id, accessor_agent_id, accessor_peer_url, resource_path, access_type) VALUES (?,?,?,?,?)",
                (self.agent_id, accessor_agent_id, accessor_peer, resource_path, access_type)
            )
            await db.commit()

    def _write_note(self, path: Path, frontmatter: dict, body: str) -> str:
        fm_lines = [f"{k}: {self._yaml_value(v)}" for k, v in frontmatter.items()]
        content = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body
        path.write_text(content, encoding="utf-8")
        return content

    def _yaml_value(self, v) -> str:
        if isinstance(v, list):
            return json.dumps(v)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return str(v).replace("\n", " ")

    async def export_broadcast(self, broadcast_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
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
        coords = self._spatial_hash(b["title"] or "", b["content_type"] or "text")
        frontmatter = {
            "id": f"broadcast_{b['id']}",
            "type": "star",
            "content_type": b.get("content_type", "text"),
            "status": b.get("status", "ready"),
            "views": b.get("view_count", 0),
            "tags": tags,
            "galaxy_x": coords[0],
            "galaxy_y": coords[1],
            "galaxy_z": coords[2],
            "galaxy_size": self._view_size(b.get("view_count", 0)),
            "galaxy_color": self._content_color(b.get("content_type", "text")),
            "constellation": tags[0] if tags else "uncategorized",
            "created": b.get("created_at", ""),
        }
        body = f"# {b.get('title', 'Untitled')}\n\n{b.get('description', '') or ''}\n\n{b.get('post_content', '') or ''}"
        safe = re.sub(r"[^\w-]", "_", (b.get("title") or "untitled")[:50])
        filename = f"{created}-{safe}.md"
        path = self.vault_path / "broadcasts" / filename
        self._write_note(path, frontmatter, body)
        await self._update_fts(filename, b.get("title", ""), body, tags)

    async def export_knowledge(self, snippet_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
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
        frontmatter = {
            "id": f"knowledge_{k['id']}",
            "type": "edge",
            "subject": k["subject"],
            "predicate": k["predicate"],
            "object": k["object"],
            "confidence": k.get("confidence", 1.0),
            "tags": tags,
            "galaxy_source_x": src[0], "galaxy_source_y": src[1], "galaxy_source_z": src[2],
            "galaxy_target_x": tgt[0], "galaxy_target_y": tgt[1], "galaxy_target_z": tgt[2],
            "galaxy_weight": k.get("confidence", 1.0),
            "created": k.get("created_at", ""),
        }
        body = f"# {k['subject']} → {k['predicate']} → {k['object']}\n\nConfidence: {k.get('confidence', 1.0)}"
        safe = re.sub(r"[^\w-]", "_", f"{k['subject']}_{k['predicate']}_{k['object']}"[:100])
        self._write_note(self.vault_path / "knowledge" / f"{safe}.md", frontmatter, body)

    async def export_trace(self, trace_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
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
        frontmatter = {
            "id": f"trace_{t['id']}",
            "type": "nebula",
            "trace_type": t.get("trace_type", "thought"),
            "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
            "galaxy_opacity": 0.2,
            "galaxy_size": min(len(msg) / 10, 100),
            "galaxy_color": "#663399",
            "created": t.get("created_at", ""),
        }
        body = f"# Ghost Trace: {t.get('trace_type', 'thought')}\n\n> {msg}"
        created = (t.get("created_at") or "unknown")[:10]
        self._write_note(self.vault_path / "traces" / f"{created}-trace-{t['id']}.md", frontmatter, body)

    async def full_sync(self):
        async with aiosqlite.connect(DB_PATH) as db:
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
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE agent_memory_vaults SET last_synced_at=datetime('now') WHERE agent_id=?",
                (self.agent_id,)
            )
            await db.commit()
        await self._write_readme()

    async def _write_readme(self):
        config = await self.get_config()
        broadcasts = len(list((self.vault_path / "broadcasts").glob("*.md")))
        knowledge = len(list((self.vault_path / "knowledge").glob("*.md")))
        traces = len(list((self.vault_path / "traces").glob("*.md")))
        desc = {
            "private": "Only you can access this vault.",
            "followers": "Verified followers can view your galaxy.",
            "federated": "Followers and whitelisted federation peers can access.",
            "public": "Open to all.",
        }.get(config.access, "")
        readme = f"""---
type: galaxy_map
agent: {self.agent_name}
access: {config.access}
last_synced: {config.last_synced or 'never'}
---

# 🌌 {self.agent_name}'s Memory Galaxy

## Privacy: {config.access.upper()} — {desc}

| Region | Count |
|--------|-------|
| broadcasts/ | {broadcasts} |
| knowledge/ | {knowledge} |
| traces/ | {traces} |
"""
        (self.vault_path / "README.md").write_text(readme)

    async def _update_fts(self, note_path: str, title: str, content: str, tags: list):
        async with aiosqlite.connect(DB_PATH) as db:
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
        offsets = {"broadcast": 0, "knowledge": 2000, "trace": 4000, "draft": 6000}
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
            if md_file.name == "README.md":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = self._parse_frontmatter(content)
                node_type = fm.get("type")
                if node_type == "star":
                    stars.append({
                        "id": fm.get("id"), "title": self._extract_title(content),
                        "x": float(fm.get("galaxy_x", 0)), "y": float(fm.get("galaxy_y", 0)),
                        "z": float(fm.get("galaxy_z", 0)), "size": float(fm.get("galaxy_size", 10)),
                        "color": fm.get("galaxy_color", "#ffffff"),
                        "constellation": fm.get("constellation", "unknown"),
                        "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
                        "content_type": fm.get("content_type", "text"),
                        "path": str(md_file.relative_to(self.vault_path)),
                        "created": str(fm.get("created", "")),
                    })
                elif node_type == "edge":
                    edges.append({
                        "id": fm.get("id"), "subject": fm.get("subject"),
                        "predicate": fm.get("predicate"), "object": fm.get("object"),
                        "source": [float(fm.get("galaxy_source_x", 0)), float(fm.get("galaxy_source_y", 0)), float(fm.get("galaxy_source_z", 0))],
                        "target": [float(fm.get("galaxy_target_x", 0)), float(fm.get("galaxy_target_y", 0)), float(fm.get("galaxy_target_z", 0))],
                        "weight": float(fm.get("galaxy_weight", 1.0)),
                        "path": str(md_file.relative_to(self.vault_path)),
                    })
                elif node_type == "nebula":
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
            "bounds": {"min": [0, 0, 0], "max": [8000, 1000, 500]},
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

    async def get_stats(self) -> dict:
        config = await self.get_config()
        stars = edges = nebulae = 0
        vault_size = 0
        for md in self.vault_path.rglob("*.md"):
            try:
                vault_size += md.stat().st_size
                fm = self._parse_frontmatter(md.read_text())
                t = fm.get("type")
                if t == "star":
                    stars += 1
                elif t == "edge":
                    edges += 1
                elif t == "nebula":
                    nebulae += 1
            except Exception:
                pass
        broadcasts_dir = self.vault_path / "broadcasts"
        knowledge_dir = self.vault_path / "knowledge"
        traces_dir = self.vault_path / "traces"
        return {
            "stars": stars,
            "edges": edges,
            "nebulae": nebulae,
            "broadcasts": len(list(broadcasts_dir.glob("*.md"))) if broadcasts_dir.exists() else 0,
            "knowledge": len(list(knowledge_dir.glob("*.md"))) if knowledge_dir.exists() else 0,
            "traces": len(list(traces_dir.glob("*.md"))) if traces_dir.exists() else 0,
            "vault_size_bytes": vault_size,
            "last_synced": config.last_synced,
            "access": config.access,
        }
