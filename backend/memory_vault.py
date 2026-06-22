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
            "last_accessed_at": k.get("last_accessed_at") or k.get("created_at", ""),
            "trust": k.get("trust_score", 0.5),
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
        await self.auto_summarize_constellations()
        await self.ensure_workspace()
        await self.generate_soul_md()

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
                        "trust": self._decayed_trust(float(fm.get("trust", 0.5)), str(fm.get("last_accessed_at", ""))),
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

    async def semantic_search(self, query: str, top_k: int = 20) -> list:
        # Try wildcard-expanded FTS5 first for partial matching
        expanded = " ".join(f"{w}*" for w in query.split() if len(w) > 2)
        async with aiosqlite.connect(DB_PATH) as db:
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
            frontmatter = {
                "id": f"summary_{safe_c[:20]}",
                "type": "star",
                "content_type": "text",
                "galaxy_x": coords[0], "galaxy_y": coords[1], "galaxy_z": coords[2],
                "galaxy_size": 14, "galaxy_color": "#c7ceea",
                "constellation": constellation,
                "tags": ["summary", constellation],
                "created": datetime.utcnow().isoformat(),
            }
            body = f"# Constellation Summary: {constellation}\n\n**{len(stars)} stars in this constellation**\n\n## Star Index\n\n"
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

    # ── Layer 1: Workspace documents ────────────────────────────────────────────

    async def ensure_workspace(self):
        """Create persistent Layer-1 workspace docs (MEMORY/USER/CREATIVE) if missing."""
        workspace = self.vault_path / "workspace"
        workspace.mkdir(exist_ok=True)

        async with aiosqlite.connect(DB_PATH) as db:
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
        async with aiosqlite.connect(DB_PATH) as db:
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
