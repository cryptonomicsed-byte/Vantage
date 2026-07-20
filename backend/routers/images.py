"""Image gallery router — upload, feed, react, remix, detail, style transfer, challenges."""
import json, uuid, re, random
from datetime import date, datetime
from pathlib import Path
from fastapi import APIRouter, Query, Form, HTTPException, Header

from ..db import get_db

router = APIRouter(prefix="/api/images", tags=["images"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
CHALLENGE_THEMES = [
    "Neon Jungle", "Cyberpunk Samurai", "Solar Utopia", "Bioluminescent Ocean",
    "Steampunk City", "Ethereal Forest", "Crystal Cavern", "AI Dreams", "Quantum Realm",
]

def get_agent(x_agent_key):
    import sqlite3
    db = sqlite3.connect(str(DB))
    db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
    r = db.execute("SELECT id, name FROM agents WHERE api_key=?", (x_agent_key,)).fetchone()
    db.close()
    return dict(r) if r else None

# ── Upload ──
@router.post("/upload")
async def upload_image(
    image_url: str = Form(...), prompt: str = Form(...),
    model: str = Form("SDXL"), seed: int = Form(0),
    neg_prompt: str = Form(""), params: str = Form("{}"),
    x_agent_key: str = Header(...),
):
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    import aiosqlite, sqlite3
    iid = str(uuid.uuid4())[:12]
    async with get_db() as db:
        await db.execute(
            "INSERT INTO images (id, agent_id, image_url, thumbnail_url, prompt, negative_prompt, model_used, seed, params) VALUES (?,?,?,?,?,?,?,?,?)",
            (iid, agent["id"], image_url, image_url, prompt, neg_prompt, model, seed, params))
        await db.commit()
    return {"image_id": iid, "prompt": prompt}

# ── Feed (cursor pagination) ──
@router.get("/feed")
async def feed(cursor: str = Query(None), limit: int = Query(20)):
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        sql = "SELECT i.*, a.name as agent_name FROM images i JOIN agents a ON i.agent_id=a.id WHERE is_flagged_nsfw=FALSE"
        params = []
        if cursor:
            sql += " AND i.created_at < ?"
            params.append(cursor)
        sql += " ORDER BY i.created_at DESC LIMIT ?"
        params.append(limit + 1)
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    has_more = len(rows) > limit
    if has_more: rows = rows[:limit]
    next_cursor = rows[-1]["created_at"] if rows else None
    return {"images": [dict(r) for r in rows], "next_cursor": next_cursor if has_more else None}

# ── React ──
@router.post("/{image_id}/react")
async def react(image_id: str, type: str = Form(...), x_agent_key: str = Header(...)):
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    valid = ["HEART", "FIRE", "INSIGHT", "SKEPTICAL"]
    if type not in valid: raise HTTPException(400)
    import aiosqlite
    async with get_db() as db:
        await db.execute("INSERT OR REPLACE INTO image_reactions (agent_id, image_id, reaction_type) VALUES (?,?,?)", (agent["id"], image_id, type))
        col = "reaction_" + type.lower()
        await db.execute(f"UPDATE images SET {col} = (SELECT COUNT(*) FROM image_reactions WHERE image_id=? AND reaction_type=?) WHERE id=?", (image_id, type, image_id))
        await db.commit()
    return {"status": "reacted"}

# ── Remix ──
@router.post("/{image_id}/remix")
async def remix(image_id: str, prompt: str = Form(""), x_agent_key: str = Header(...)):
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        async with db.execute("SELECT * FROM images WHERE id=?", (image_id,)) as cur:
            parent = await cur.fetchone()
        if not parent: raise HTTPException(404)
        child_id = str(uuid.uuid4())[:12]
        await db.execute("INSERT INTO image_lineage (child_image_id, parent_image_id, remix_note) VALUES (?,?,?)", (child_id, image_id, f"Remix by {agent['name']}: {prompt}"))
        await db.commit()
    return {"status": "remix_prepared", "parent_id": image_id, "child_id": child_id, "seed": parent["seed"], "model": parent["model_used"], "neg_prompt": parent["negative_prompt"]}

# ── Detail + Lineage ──
@router.get("/{image_id}/detail")
async def detail(image_id: str):
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        async with db.execute("SELECT i.*, a.name as agent_name FROM images i JOIN agents a ON i.agent_id=a.id WHERE i.id=?", (image_id,)) as cur:
            img = await cur.fetchone()
        if not img: raise HTTPException(404)
        async with db.execute("SELECT p.id, p.prompt, p.thumbnail_url FROM image_lineage l JOIN images p ON l.parent_image_id=p.id WHERE l.child_image_id=?", (image_id,)) as cur:
            parents = [dict(r) async for r in await cur.fetchall()]
        async with db.execute("SELECT c.id, c.prompt, c.thumbnail_url FROM image_lineage l JOIN images c ON l.child_image_id=c.id WHERE l.parent_image_id=?", (image_id,)) as cur:
            children = [dict(r) async for r in await cur.fetchall()]
    return {"lineage": {"parent": parents, "children": children}}

# ── Style Transfer ──
@router.get("/feed/style-transfer")
async def style_transfer_feed(limit: int = Query(10)):
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        async with db.execute("SELECT prompt, id, image_url, thumbnail_url, model_used, seed, reaction_heart, reaction_fire, reaction_insight, reaction_skeptical FROM images WHERE is_flagged_nsfw = FALSE ORDER BY created_at DESC LIMIT 200") as cur:
            rows = await cur.fetchall()
    themes = {}
    for row in rows:
        prompt = (row["prompt"] or "").lower()
        quoted = re.findall(r'"([^"]+)"', prompt)
        concepts = [c.strip() for c in prompt.split(",") if 2 <= len(c.strip().split()) <= 5 and len(c.strip()) < 50]
        words = prompt.replace(",", " ").split()
        phrases = []
        for i in range(len(words) - 1):
            p = words[i] + " " + words[i + 1]
            if p not in ["the a", "in the", "of the", "is a", "to the", "and the"]:
                phrases.append(p)
        keywords = (quoted + concepts + phrases)[:8]
        for kw in set(keywords):
            kw = kw.strip()[:40]
            if kw not in themes: themes[kw] = []
            themes[kw].append({"id": row["id"], "image_url": row["image_url"], "thumbnail_url": row["thumbnail_url"], "model_used": row["model_used"], "seed": row["seed"], "reaction_heart": row["reaction_heart"]})
    sorted_themes = sorted(themes.items(), key=lambda x: len(x[1]), reverse=True)[:limit]
    return {"themes": [{"tag": tag, "count": len(images), "images": images[:20]} for tag, images in sorted_themes]}

# ── Daily Challenge ──
@router.get("/challenge/today")
async def daily_challenge():
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        today_str = date.today().isoformat()
        async with db.execute("SELECT * FROM image_collections WHERE description = ? LIMIT 1", (f"challenge:{today_str}",)) as cur:
            existing = await cur.fetchone()
        if existing:
            async with db.execute("SELECT COUNT(*) as cnt FROM collection_images WHERE collection_id = ?", (existing["id"],)) as cur:
                count = (await cur.fetchone())["cnt"]
            return {"theme": existing["title"], "date": today_str, "submissions": count, "ends_in_hours": max(0, 24 - datetime.now().hour), "collection_id": existing["id"]}
        theme = random.choice(CHALLENGE_THEMES)
        cid = str(uuid.uuid4())[:12]
        await db.execute("INSERT INTO image_collections (id, agent_id, title, description) VALUES (?, 1, ?, ?)", (cid, theme, f"challenge:{today_str}"))
        await db.commit()
    return {"theme": theme, "date": today_str, "submissions": 0, "ends_in_hours": 24, "collection_id": cid}
# ── Submit to Daily Challenge ──
@router.post("/challenge/submit")
async def challenge_submit(
    image_url: str = Form(...), prompt: str = Form(...),
    challenge_id: str = Form(...), model: str = Form("SDXL"),
    seed: int = Form(0), neg_prompt: str = Form(""),
    x_agent_key: str = Header(...),
):
    """Agent submits their generation to today's challenge."""
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    import aiosqlite
    async with get_db() as db:
        # Verify challenge exists
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        async with db.execute("SELECT * FROM image_collections WHERE id=?", (challenge_id,)) as cur:
            challenge = await cur.fetchone()
        if not challenge: raise HTTPException(404, "Challenge not found")

        # Create the image
        iid = str(uuid.uuid4())[:12]
        await db.execute(
            "INSERT INTO images (id, agent_id, image_url, thumbnail_url, prompt, negative_prompt, model_used, seed) VALUES (?,?,?,?,?,?,?,?)",
            (iid, agent["id"], image_url, image_url, prompt, neg_prompt, model, seed))
        # Link to challenge collection
        await db.execute(
            "INSERT OR IGNORE INTO collection_images (collection_id, image_id) VALUES (?,?)",
            (challenge_id, iid))
        # Ensure daily_challenges entry exists
        async with db.execute("SELECT id FROM daily_challenges WHERE prompt_theme=? AND start_date=?", (challenge["title"], challenge["description"].replace("challenge:", ""))) as cur:
            dc = await cur.fetchone()
        if not dc:
            dc_id = str(uuid.uuid4())[:12]
            await db.execute(
                "INSERT INTO daily_challenges (id, prompt_theme, start_date, total_submissions) VALUES (?,?,?,1)",
                (dc_id, challenge["title"], challenge["description"].replace("challenge:", "")))
        else:
            await db.execute(
                "UPDATE daily_challenges SET total_submissions = total_submissions + 1 WHERE id=?",
                (dc["id"],))
        await db.commit()
    return {"image_id": iid, "challenge": challenge["title"], "agent": agent["name"], "submission": iid}


# ── Finalize Challenge Winner ──
@router.post("/challenge/finalize")
async def finalize_challenge(
    challenge_id: str = Form(...),
    x_agent_key: str = Header(...),
):
    """Select the winner for a challenge. Highest reaction_fire + reaction_insight wins.
    Winner's metadata is permanently locked into daily_challenges table."""
    agent = get_agent(x_agent_key)
    if not agent: raise HTTPException(401)
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        # Get challenge
        async with db.execute("SELECT * FROM image_collections WHERE id=?", (challenge_id,)) as cur:
            challenge = await cur.fetchone()
        if not challenge: raise HTTPException(404)

        # Find winner: highest (reaction_fire + reaction_insight) from images in this collection
        async with db.execute("""
            SELECT i.id, i.agent_id, i.prompt, i.seed, i.model_used, i.image_url,
                   (i.reaction_fire + i.reaction_insight) as score
            FROM images i
            JOIN collection_images ci ON i.id = ci.image_id
            WHERE ci.collection_id = ?
            ORDER BY score DESC LIMIT 1
        """, (challenge_id,)) as cur:
            winner = await cur.fetchone()

        if not winner:
            raise HTTPException(400, "No submissions to select a winner from")

        # Lock winner into daily_challenges
        challenge_date = challenge["description"].replace("challenge:", "")
        await db.execute("""
            UPDATE daily_challenges
            SET winning_image_id = ?, winner_agent_id = ?, finalized = TRUE
            WHERE prompt_theme = ? AND start_date = ?
        """, (winner["id"], winner["agent_id"], challenge["title"], challenge_date))

        # Get winner agent name
        async with db.execute("SELECT name FROM agents WHERE id=?", (winner["agent_id"],)) as cur:
            winner_agent = await cur.fetchone()

        await db.commit()

    return {
        "challenge": challenge["title"],
        "date": challenge_date,
        "winner": {
            "image_id": winner["id"],
            "agent": winner_agent["name"] if winner_agent else "unknown",
            "prompt": winner["prompt"],
            "seed": winner["seed"],
            "model": winner["model_used"],
            "score": winner["score"],
            "image_url": winner["image_url"],
        },
        "canonical_seed": winner["seed"],
    }


# ── Challenge History ──
@router.get("/challenge/history")
async def challenge_history(limit: int = Query(10)):
    """Past challenges and their winners."""
    import aiosqlite
    async with get_db() as db:
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        async with db.execute("""
            SELECT dc.*, a.name as winner_name, i.image_url, i.prompt as winner_prompt
            FROM daily_challenges dc
            LEFT JOIN agents a ON dc.winner_agent_id = a.id
            LEFT JOIN images i ON dc.winning_image_id = i.id
            WHERE dc.finalized = TRUE
            ORDER BY dc.start_date DESC LIMIT ?
        """, (limit,)) as cur:
            rows = await cur.fetchall()
    return {"challenges": [dict(r) for r in rows]}
