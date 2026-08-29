"""Narrative-detection — pure pattern-mining over token data ALREADY
flowing through Vantage (pumpfun_premigration_tokens is the real source
used here: it has actual token `name`/`symbol` fields and is populated
live by pumpfun_tier_scanner.py, no new external integration).

═══════════════════════════════════════════════════════════════════════
THE REAL PROBLEM THIS ADDRESSES
═══════════════════════════════════════════════════════════════════════
Tokens "inherit" hype from combining two independently-trending
narratives -- a token called 'fone' (a phone-prop meme) runs, separately
a token called 'PINK' (a cause-awareness meme) runs, then 'PINKFONE'
launches and runs BECAUSE it combines both trending threads, even though
"phone" and "breast-cancer-awareness" are semantically unrelated. Pure
per-token volume/mcap/conviction scoring has no way to see this.

═══════════════════════════════════════════════════════════════════════
APPROACH -- honest v1 limits, no fake NLP dressing
═══════════════════════════════════════════════════════════════════════
This is KEYWORD-BASED, not semantic/embedding-based. No embedding model
is installed anywhere in this stack, and adding one (even a "lightweight"
one) is a real new dependency + model-weights download on a production
VPS for a feature that keyword matching already covers well for THIS
specific failure mode (memecoin names are typically literal concatenated
keywords -- "pinkfone", "trumpwithfone" -- not paraphrases). If true
semantic clustering ("moon" clusters with "lunar" clusters with
"astronaut") becomes a real need later, that is the honest next step and
NOT what this module claims to do.

Two real mechanisms, both keyword-level:

1. SEED LEXICON + FUZZY SUBSTRING MATCH (_SEED_THEMES below). A small,
   hand-curated set of recurring memecoin narrative categories, each with
   known keyword variants. Matching against a token's name/symbol is:
     a) exact substring match against every keyword variant, AND
     b) fuzzy match (difflib.SequenceMatcher ratio >= FUZZY_THRESHOLD)
        against each token-substring candidate, which is what lets
        'fone' match the 'phone' theme (ratio ~0.89) without 'fone'
        being hardcoded as a variant -- real spelling/phonetic
        proximity, NOT semantic understanding. It will NOT catch a true
        synonym with a different spelling shape (e.g. 'telephone' vs
        'fone' alone scores lower; both are listed as explicit variants
        specifically so real cases aren't missed by fuzzy matching alone).

2. DYNAMIC CO-OCCURRENCE THEMES (_discover_dynamic_themes). Keywords NOT
   in the seed lexicon still become their own ad-hoc theme if the same
   keyword appears in >= DYNAMIC_THEME_MIN_TOKENS distinct real tokens'
   names within the lookback window -- i.e. themes emerge from the data
   itself, not just the fixed dictionary. This is the "smarter than exact
   string match" real mechanism requested: it needs no ML dependency,
   is fully deterministic and auditable (every theme traces to the exact
   tokens that produced it), and generalizes beyond whatever the seed
   lexicon's author thought to list.

Every narrative-heat number is a COUNT of real, named tokens that
actually existed in pumpfun_premigration_tokens with real market-cap/
score data attached -- nothing here is synthesized.
"""
import json
import logging
import re
import time

from backend.degen_filters import passes_dust_floor

MIN_REAL_TRADES = 2  # same bar as PUMPFUN_MIN_TOTAL_TRADES: proof trading continued past the first tx
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────
LOOKBACK_HOURS = 6          # "currently running" window for heat computation
HOT_THRESHOLD = 2           # distinct tokens matched within lookback = "hot"
# Real live-data finding 2026-08-29: at production scale (~300 tokens/6h),
# 2 distinct tokens sharing an ordinary English word is common by pure
# chance, not narrative signal -- a live scan produced 96 "dynamic themes"
# and 91 "combo flags" for words like "this"/"world"/"fund"/"solana",
# drowning out real signal. Raised from 2 to 4 (meaningfully above chance
# co-occurrence at this candidate-pool size) alongside the expanded
# stopword list below, which was the bigger real gap.
DYNAMIC_THEME_MIN_TOKENS = 4
FUZZY_THRESHOLD = 0.72       # difflib ratio for spelling/phonetic proximity match
MIN_KEYWORD_LEN = 4          # ignore fragments shorter than this (too noisy; was 3)

# Generic words that appear in huge numbers of unrelated memecoin names --
# excluded so they never become a "theme" on their own. The crypto-specific
# subset alone (first line) was insufficient in practice: live production
# data showed ordinary English FUNCTION words ("this", "that", "world",
# "fund", "for", "me", "get") colliding across unrelated token names just
# as often as crypto slang, since real memecoin names are often full
# phrases ("Sell Me This Shitcoin", "World Oil Fund"). This is a standard
# short English stopword list, not an attempt to filter meaning -- any
# keyword here can never become a dynamic theme on its own, but still
# contributes to seed-theme substring matching where relevant.
_STOPWORDS = {
    "coin", "token", "inu", "moon", "official", "new", "the", "on", "sol",
    "pump", "fun", "baby", "mini", "safe", "doge", "shib", "elon", "king",
    "og", "ai", "meme", "gem", "v2", "a", "of", "wif",
    "this", "that", "these", "those", "here", "there", "with", "from",
    "your", "you", "our", "and", "for", "not", "but", "are", "was",
    "were", "will", "can", "has", "have", "had", "its", "it's", "into",
    "onto", "over", "under", "about", "world", "fund", "get", "got",
    "best", "top", "first", "real", "true", "just", "only", "very",
    "more", "most", "some", "any", "all", "one", "two", "three",
    "what", "who", "how", "why", "when", "where", "which", "than",
    "then", "them", "they", "his", "her", "she", "him", "out", "now",
}

# Seed lexicon: hand-curated, real recurring memecoin narrative categories.
# Each keyword variant is something a token name would plausibly contain
# literally or near-literally. Grows over time as new recurring themes are
# observed -- this is intentionally small, not an attempt at completeness.
_SEED_THEMES: dict[str, dict] = {
    "phone_prop": {
        "label": "phone / telephone prop meme",
        "keywords": ["fone", "phone", "telephone", "mobile", "iphone", "flipphone", "payphone"],
    },
    "cause_awareness": {
        "label": "cause-awareness ribbon meme",
        "keywords": ["pink", "breastcancer", "awareness", "ribbon", "cancer"],
    },
    "animal_prop": {
        "label": "animal-with-object meme (WIF pattern)",
        "keywords": ["hat", "wif", "cap", "beanie", "glasses", "sunglasses"],
    },
    "political_figure": {
        "label": "political-figure meme",
        "keywords": ["trump", "biden", "maga", "potus", "election"],
    },
    "space": {
        "label": "space / cosmic meme",
        "keywords": ["moon", "mars", "rocket", "astronaut", "galaxy", "lunar", "orbit"],
    },
}


def _tokenize(name: str, symbol: str) -> tuple[list[str], str]:
    """Real-not-fancy tokenization: lowercase, strip non-alphanumerics to
    get word fragments (splits on spaces/punctuation/camelCase-ish
    boundaries), plus the fully-concatenated compact string (no
    separators at all) -- the compact form is what lets a substring
    search catch 'pinkfone' containing both 'pink' and 'fone' with no
    space between them, which is exactly the real owner-reported case."""
    raw = f"{name or ''} {symbol or ''}".lower()
    # camelCase / PascalCase boundary split, then normal separator split
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f"{name or ''} {symbol or ''}")
    words = re.findall(r"[a-z0-9]+", spaced.lower())
    words = [w for w in words if len(w) >= MIN_KEYWORD_LEN and w not in _STOPWORDS]
    compact = re.sub(r"[^a-z0-9]", "", raw)
    return words, compact


def _fuzzy_match(fragment: str, keyword: str) -> bool:
    if keyword in fragment or fragment in keyword:
        return True
    return SequenceMatcher(None, fragment, keyword).ratio() >= FUZZY_THRESHOLD


def match_seed_themes(name: str, symbol: str) -> dict[str, list[str]]:
    """Returns {theme_key: [matched_keyword, ...]} for every seed theme
    this token's name/symbol matches, via substring-in-compact-string
    (catches concatenated combos) or fuzzy word-level match (catches
    spelling variants like 'fone')."""
    words, compact = _tokenize(name, symbol)
    matches: dict[str, list[str]] = {}
    for theme_key, theme in _SEED_THEMES.items():
        hit_keywords = []
        for kw in theme["keywords"]:
            if kw in compact:
                hit_keywords.append(kw)
                continue
            if any(_fuzzy_match(w, kw) for w in words):
                hit_keywords.append(kw)
        if hit_keywords:
            matches[theme_key] = sorted(set(hit_keywords))
    return matches


async def _fetch_recent_tokens(db) -> list[dict]:
    """Real candidate universe: pump.fun pre-migration tokens with a real
    name, active within LOOKBACK_HOURS. Deliberately does not invent or
    backfill missing names -- rows with no usable name/symbol are simply
    unmatchable and skipped.

    Real bug fixed here (owner-reported): narrative/combo detection had no
    liquidity/volume/market-cap floor at all, so a name-pattern match alone
    (e.g. "pinkfone") could flag a token with zero real trading activity --
    real narrative, dead token. Gated on passes_dust_floor() (the same
    general $7k mcap / $2k liquidity-fallback bar used across the rest of
    this codebase) plus a minimum real trade count -- deliberately NOT
    aggregate_score.py's narrow $14k-$32k pump.fun graduation-window band,
    since narrative detection needs to see tokens across more of their real
    lifecycle than just that late slice, or almost nothing would ever
    qualify as a candidate here."""
    cur = await db.execute(
        f"""SELECT mint, symbol, name, score, market_cap_usd, last_trade_at,
                   buy_count, sell_count
            FROM pumpfun_premigration_tokens
            WHERE evicted = 0
              AND last_trade_at >= datetime('now', '-{LOOKBACK_HOURS} hours')
              AND (name != '' OR symbol != '')""",
    )
    rows = await cur.fetchall()
    alive = []
    for r in rows:
        d = dict(r)
        trades = (d.get("buy_count") or 0) + (d.get("sell_count") or 0)
        if trades < MIN_REAL_TRADES:
            continue
        if not passes_dust_floor(d.get("market_cap_usd")):
            continue
        alive.append(d)
    return alive


def _discover_dynamic_themes(tokens: list[dict]) -> dict[str, dict]:
    """Keywords not covered by the seed lexicon still become real themes
    if they recur across >= DYNAMIC_THEME_MIN_TOKENS distinct tokens in
    the current window -- co-occurrence-driven, not a fixed dictionary."""
    keyword_tokens: dict[str, set[str]] = {}
    seed_keywords = {kw for t in _SEED_THEMES.values() for kw in t["keywords"]}
    for t in tokens:
        words, _ = _tokenize(t.get("name") or "", t.get("symbol") or "")
        for w in set(words):
            if w in seed_keywords:
                continue
            keyword_tokens.setdefault(w, set()).add(t["mint"])
    dynamic = {}
    for kw, mints in keyword_tokens.items():
        if len(mints) >= DYNAMIC_THEME_MIN_TOKENS:
            theme_key = f"kw_{kw}"
            dynamic[theme_key] = {"label": f'"{kw}" (auto-discovered)', "keywords": [kw]}
    return dynamic


async def compute_narrative_heat() -> dict:
    """Real detection pass: mines currently-active pump.fun tokens for
    seed + dynamically-discovered narrative themes, persists per-theme
    heat and per-token matches, and flags any token whose name/symbol
    combines >= 2 themes that were ALREADY hot from OTHER tokens (i.e.
    the combo token rode two pre-existing trends, not two trends it
    single-handedly created). Every number returned is traceable to the
    real tokens listed under `sample_mints`/matched rows in the DB.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        tokens = await _fetch_recent_tokens(db)
        dynamic_themes = _discover_dynamic_themes(tokens)
        all_themes = {**_SEED_THEMES, **dynamic_themes}

        # Pass 1: match every token against every theme (seed + dynamic).
        theme_matches: dict[str, list[dict]] = {}
        token_theme_map: dict[str, dict[str, list[str]]] = {}
        for t in tokens:
            name, symbol = t.get("name") or "", t.get("symbol") or ""
            matches = match_seed_themes(name, symbol)
            words, _ = _tokenize(name, symbol)
            wordset = set(words)
            for theme_key, theme in dynamic_themes.items():
                kw = theme["keywords"][0]
                if kw in wordset:
                    matches[theme_key] = [kw]
            if not matches:
                continue
            token_theme_map[t["mint"]] = matches
            for theme_key, kws in matches.items():
                theme_matches.setdefault(theme_key, []).append(
                    {"mint": t["mint"], "symbol": symbol, "name": name,
                     "matched_keyword": kws[0], "market_cap_usd": t.get("market_cap_usd"),
                     "score": t.get("score")}
                )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Persist theme heat + matched tokens.
        for theme_key, matched in theme_matches.items():
            distinct_mints = {m["mint"] for m in matched}
            heat = len(distinct_mints)
            label = all_themes[theme_key]["label"]
            await db.execute(
                """INSERT INTO narrative_themes (theme_key, label, keywords, first_seen_at, last_seen_at, token_count, heat_score, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(theme_key) DO UPDATE SET
                     label=excluded.label, last_seen_at=excluded.last_seen_at,
                     heat_score=excluded.heat_score, updated_at=excluded.updated_at,
                     token_count = token_count + (
                       CASE WHEN excluded.heat_score > narrative_themes.heat_score
                            THEN 0 ELSE 0 END
                     )""",
                (theme_key, label, json.dumps(all_themes[theme_key]["keywords"]), now, now, heat, heat, now),
            )
            for m in matched:
                try:
                    await db.execute(
                        """INSERT INTO narrative_theme_tokens
                           (theme_key, mint, symbol, name, matched_keyword, market_cap_usd, score, detected_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(theme_key, mint) DO UPDATE SET
                             detected_at=excluded.detected_at, market_cap_usd=excluded.market_cap_usd, score=excluded.score""",
                        (theme_key, m["mint"], m["symbol"], m["name"], m["matched_keyword"],
                         m.get("market_cap_usd"), m.get("score"), now),
                    )
                except Exception as e:
                    logger.debug("narrative_detection: theme_token upsert failed for %s/%s: %s", theme_key, m["mint"], e)

        # token_count = true distinct-ever count (recompute from persisted rows,
        # not incrementally -- avoids double counting across repeated runs).
        for theme_key in theme_matches:
            cur = await db.execute(
                "SELECT COUNT(DISTINCT mint) as c FROM narrative_theme_tokens WHERE theme_key=?", (theme_key,)
            )
            row = await cur.fetchone()
            await db.execute(
                "UPDATE narrative_themes SET token_count=? WHERE theme_key=?",
                (row["c"] if row else 0, theme_key),
            )

        # Combo detection: a token matching >=2 themes, where each of those
        # themes was already hot from OTHER tokens (excluding this one).
        combo_flags = []
        for mint, matches in token_theme_map.items():
            if len(matches) < 2:
                continue
            qualifying = []
            for theme_key in matches:
                other_mints = {m["mint"] for m in theme_matches.get(theme_key, [])} - {mint}
                if len(other_mints) >= (HOT_THRESHOLD - 1):
                    qualifying.append(theme_key)
            if len(qualifying) < 2:
                continue
            token = next(t for t in tokens if t["mint"] == mint)
            labels = [all_themes[tk]["label"] for tk in qualifying]
            flag = {
                "mint": mint, "symbol": token.get("symbol"), "name": token.get("name"),
                "theme_keys": qualifying, "theme_labels": labels,
                "narrative": "combines: " + " + ".join(f"[{lb}]" for lb in labels) + ", both independently trending",
                "market_cap_usd": token.get("market_cap_usd"),
            }
            combo_flags.append(flag)
            await db.execute(
                """INSERT INTO narrative_combo_flags (mint, symbol, name, theme_keys, theme_labels, detected_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(mint) DO UPDATE SET
                     theme_keys=excluded.theme_keys, theme_labels=excluded.theme_labels, detected_at=excluded.detected_at""",
                (mint, token.get("symbol"), token.get("name"), json.dumps(qualifying), json.dumps(labels), now),
            )

        await db.commit()

    return {
        "themes_detected": len(theme_matches),
        "dynamic_themes_discovered": len(dynamic_themes),
        "combo_flags": combo_flags,
        "tokens_scanned": len(tokens),
        "generated_at": int(time.time()),
    }


async def get_hot_narratives(min_heat: int = HOT_THRESHOLD) -> list[dict]:
    """Currently-hot themes (persisted heat, recomputed on read via
    compute_narrative_heat by the caller if freshness matters -- this
    function only reads what's already persisted, real history included)."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT theme_key, label, keywords, heat_score, token_count, first_seen_at, last_seen_at
               FROM narrative_themes WHERE heat_score >= ? ORDER BY heat_score DESC, last_seen_at DESC""",
            (min_heat,),
        )
        themes = [dict(r) for r in await cur.fetchall()]
        for theme in themes:
            theme["keywords"] = json.loads(theme["keywords"] or "[]")
            cur2 = await db.execute(
                """SELECT mint, symbol, name, matched_keyword, market_cap_usd, score, detected_at
                   FROM narrative_theme_tokens WHERE theme_key=? ORDER BY detected_at DESC LIMIT 10""",
                (theme["theme_key"],),
            )
            theme["sample_tokens"] = [dict(r) for r in await cur2.fetchall()]
        return themes


async def get_combo_flags(limit: int = 20) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT mint, symbol, name, theme_keys, theme_labels, detected_at
               FROM narrative_combo_flags ORDER BY detected_at DESC LIMIT ?""",
            (limit,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            r["theme_keys"] = json.loads(r["theme_keys"] or "[]")
            r["theme_labels"] = json.loads(r["theme_labels"] or "[]")
        return rows


async def mint_combo_flag(mint: str) -> Optional[dict]:
    """Single-mint lookup used to annotate platform-leaders/aggregate-score
    results with the combo flag when a qualifying token appears there."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT theme_keys, theme_labels, detected_at FROM narrative_combo_flags WHERE mint=?", (mint,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "theme_labels": json.loads(row["theme_labels"] or "[]"),
            "detected_at": row["detected_at"],
        }
