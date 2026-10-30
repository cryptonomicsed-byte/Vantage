"""Julia memory engine client — resonance scoring, semantic recall, glyph similarity."""
import os, httpx

MEMORY_URL = os.environ.get("MEMORY_ENGINE_URL", "http://localhost:7703")

async def store_glyph(glyph_id: str, text: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{MEMORY_URL}/store", json={"id": glyph_id, "text": text})
            return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def search_memory(query: str, limit: int = 10) -> list:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{MEMORY_URL}/search", json={"query": query, "limit": limit})
            return r.json().get("results", [])
    except Exception:
        return []

async def resonance(text_a: str, text_b: str) -> float:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{MEMORY_URL}/resonance", json={"a": text_a, "b": text_b})
            return r.json().get("score", 0.0)
    except Exception:
        return 0.0
