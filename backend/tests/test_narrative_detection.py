"""Tests for backend/narrative_detection.py -- real keyword-pattern
narrative mining. Covers: seed-theme substring matching (concatenated
combos like 'PINKFONE'), fuzzy spelling match ('fone' -> phone_prop
theme), dynamic co-occurrence theme discovery, heat persistence, and the
combo-flag detection (a token must combine 2 themes that are ALREADY hot
from OTHER tokens, not just self-referentially hot).
"""
import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from backend.db import DB_PATH, init_agents_db
from backend.narrative_detection import (
    match_seed_themes,
    compute_narrative_heat,
    get_hot_narratives,
    get_combo_flags,
    mint_combo_flag,
)


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _insert_token(db, mint: str, symbol: str, name: str, **overrides):
    row = dict(
        mint=mint, symbol=symbol, name=name,
        market_cap_usd=20000.0, score=10.0, manipulation_flags="[]",
        evicted=0, migrated=0, last_trade_at=_now_ts(),
    )
    row.update(overrides)
    await db.execute(
        """INSERT INTO pumpfun_premigration_tokens
           (mint, symbol, name, market_cap_usd, score, manipulation_flags, evicted, migrated, last_trade_at)
           VALUES (:mint,:symbol,:name,:market_cap_usd,:score,:manipulation_flags,:evicted,:migrated,:last_trade_at)""",
        row,
    )


@pytest.fixture(autouse=True)
async def _init_schema():
    await init_agents_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pumpfun_premigration_tokens")
        await db.execute("DELETE FROM narrative_themes")
        await db.execute("DELETE FROM narrative_theme_tokens")
        await db.execute("DELETE FROM narrative_combo_flags")
        await db.commit()


# ── match_seed_themes: pure function, no DB ──────────────────────────────

def test_exact_keyword_substring_match():
    matches = match_seed_themes("Trump Phone", "TRUMPFONE")
    assert "phone_prop" in matches
    assert "political_figure" in matches


def test_concatenated_combo_matches_both_themes_via_compact_string():
    # The exact owner-reported case: PINKFONE with no separator between
    # 'pink' and 'fone' must still match both themes.
    matches = match_seed_themes("PinkFone", "PINKFONE")
    assert "cause_awareness" in matches
    assert "pink" in matches["cause_awareness"]
    assert "phone_prop" in matches
    assert "fone" in matches["phone_prop"]


def test_fuzzy_spelling_variant_matches_without_exact_keyword():
    # 'fone' is a listed variant already, but confirm a genuinely NOT-listed
    # near-miss spelling ('phoen', a transposition of 'phone') still
    # fuzzy-matches via difflib ratio, proving the fuzzy path (not just the
    # explicit variant list) does real work.
    matches = match_seed_themes("Phoen Token", "PHOEN")
    assert "phone_prop" in matches


def test_unrelated_name_matches_nothing():
    matches = match_seed_themes("Generic Dog Coin", "GDOG")
    assert matches == {} or "animal_prop" not in matches


def test_stopword_only_name_does_not_crash():
    matches = match_seed_themes("Coin Moon Token", "CMT")
    # "moon" is in both stopwords AND the space seed theme's keywords --
    # stopwords only gate DYNAMIC discovery, not seed-theme matching, so
    # this legitimately matches "space".
    assert "space" in matches


# ── compute_narrative_heat: real DB-driven detection ─────────────────────

@pytest.mark.asyncio
async def test_two_tokens_sharing_a_theme_make_it_hot():
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_token(db, "Mint1" + "1" * 39, "FONE1", "First Fone Coin")
        await _insert_token(db, "Mint2" + "2" * 39, "FONE2", "Second Phone Token")
        await db.commit()

    result = await compute_narrative_heat()
    assert result["tokens_scanned"] == 2

    hot = await get_hot_narratives()
    hot_keys = {t["theme_key"] for t in hot}
    assert "phone_prop" in hot_keys
    phone_theme = next(t for t in hot if t["theme_key"] == "phone_prop")
    assert phone_theme["heat_score"] >= 2
    assert len(phone_theme["sample_tokens"]) >= 2
    mints_in_sample = {t["mint"] for t in phone_theme["sample_tokens"]}
    assert "Mint1" + "1" * 39 in mints_in_sample


@pytest.mark.asyncio
async def test_single_token_theme_is_not_hot():
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_token(db, "Solo1" + "1" * 39, "SOLOFONE", "Solo Phone Coin")
        await db.commit()

    await compute_narrative_heat()
    hot = await get_hot_narratives()
    assert "phone_prop" not in {t["theme_key"] for t in hot}


@pytest.mark.asyncio
async def test_dynamic_theme_discovered_from_cooccurring_unlisted_keyword():
    # "zorbatron" is in no seed lexicon at all -- two tokens both using it
    # must still surface as a real, auto-discovered theme.
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_token(db, "Zor1" + "1" * 39, "ZORB1", "Zorbatron Prime")
        await _insert_token(db, "Zor2" + "2" * 39, "ZORB2", "Baby Zorbatron")
        await db.commit()

    result = await compute_narrative_heat()
    assert result["dynamic_themes_discovered"] >= 1
    hot = await get_hot_narratives()
    dynamic_keys = [t for t in hot if t["theme_key"] == "kw_zorbatron"]
    assert len(dynamic_keys) == 1
    assert dynamic_keys[0]["heat_score"] >= 2


@pytest.mark.asyncio
async def test_real_owner_scenario_combo_flag_fires_after_both_narratives_hot():
    # fone tokens (2) + pink tokens (2) independently trending, then a
    # combo token launches riding both -- must be flagged, exactly the
    # real bug scenario in the owner's task description.
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_token(db, "F1" + "1" * 39, "FONE", "Fone Meme")
        await _insert_token(db, "F2" + "1" * 39, "PHONE2", "Old School Phone")
        await _insert_token(db, "P1" + "1" * 39, "PINK", "Pink Awareness Coin")
        await _insert_token(db, "P2" + "1" * 39, "RIBBON", "Cancer Ribbon Token")
        await _insert_token(db, "C1" + "1" * 39, "PINKFONE", "PinkFone")
        await db.commit()

    result = await compute_narrative_heat()
    combo_mints = {f["mint"] for f in result["combo_flags"]}
    assert "C1" + "1" * 39 in combo_mints

    combo = next(f for f in result["combo_flags"] if f["mint"] == "C1" + "1" * 39)
    assert set(combo["theme_keys"]) == {"phone_prop", "cause_awareness"}
    assert "combines:" in combo["narrative"]

    # Persisted and independently retrievable.
    persisted = await get_combo_flags()
    assert any(f["mint"] == "C1" + "1" * 39 for f in persisted)
    single = await mint_combo_flag("C1" + "1" * 39)
    assert single is not None
    assert "phone / telephone prop meme" in single["theme_labels"]


@pytest.mark.asyncio
async def test_combo_flag_requires_narratives_hot_from_OTHER_tokens_not_itself():
    # Only ONE fone token and ONE pink token exist, and they're the same
    # combo token's own matches -- i.e. this token would self-report as
    # matching 2 themes, but neither theme has an independent second
    # token, so no real "two trends colliding" occurred. Must NOT flag.
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_token(db, "Solo" + "1" * 39, "PINKFONE", "PinkFone")
        await db.commit()

    result = await compute_narrative_heat()
    assert result["combo_flags"] == []


@pytest.mark.asyncio
async def test_evicted_tokens_excluded_from_scan():
    async with aiosqlite.connect(DB_PATH) as db:
        await _insert_token(db, "Evict1" + "1" * 39, "FONE1", "Evicted Fone", evicted=1)
        await _insert_token(db, "Evict2" + "1" * 39, "FONE2", "Live Fone Token", evicted=0)
        await db.commit()

    result = await compute_narrative_heat()
    assert result["tokens_scanned"] == 1
