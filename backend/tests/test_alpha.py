"""Alpha engine + live-intel scoring endpoint.

Covers the pure primitives (robustness, composite, feature assembly) and the
end-to-end /api/alpha/token/{ident} path that assembles features from real
signals (social_signals rows + the in-memory intel pool) and scores them.
"""
import aiosqlite
import pytest

from backend.alpha_engine import (
    composite_alpha_score,
    mandelbrot_robustness,
    assemble_features,
    derive_social,
    derive_security,
    derive_concentration,
)


# ── Pure primitives ──────────────────────────────────────────────────────────

def test_robustness_monotonic_real_axis():
    """A clean token stays bounded (robust); a rug-shaped one escapes fast."""
    clean = mandelbrot_robustness(velocity=0.5, concentration=0.1, security=0.9)
    rug = mandelbrot_robustness(velocity=0.99, concentration=0.9, security=0.1)
    assert clean.bounded and clean.verdict == "robust momentum"
    assert not rug.bounded and rug.risk > clean.risk


def test_composite_gate_penalises_fragile():
    """Same features but a fragile structure can never out-grade a robust one."""
    robust = composite_alpha_score(0.9, 0.5, 0.1, 0.8, 0.9)
    fragile = composite_alpha_score(0.9, 0.99, 0.95, 0.8, 0.1)
    assert robust["score"] > fragile["score"]
    assert fragile["recommendation"] in ("watch", "skip")


def test_derive_social_bull_vs_bear():
    bull = [{"type": "call", "direction": "BULLISH", "conviction": 0.8},
            {"type": "mention", "direction": "BULLISH", "conviction": 0.6}]
    bear = [{"type": "mention", "direction": "BEARISH", "conviction": 0.9}]
    s_bull, _ = derive_social(bull)
    s_bear, _ = derive_social(bear)
    s_none, prov = derive_social([])
    assert s_bull > 0.5 > s_bear
    assert s_none == 0.5 and prov["n"] == 0


def test_derive_security_collapses_on_poison():
    safe, _ = derive_security([{"type": "mention", "direction": "BULLISH", "conviction": 0.5}])
    poisoned, prov = derive_security(
        [{"type": "poison_alert", "source": "poison_radar", "conviction": 0.9, "detail": "rug"}])
    assert safe > poisoned
    assert poisoned < 0.3 and prov["n"] == 1


def test_derive_concentration_parses_pct():
    conc, prov = derive_concentration(
        [{"detail": "Top 5 holders own 34% — concentration risk"}])
    assert abs(conc - 0.34) < 1e-6
    assert prov["top_holders_pct"] == 0.34


def test_assemble_respects_overrides():
    feats, prov = assemble_features(
        [], overrides={"wallet_quality": 0.9}, velocity_hint=0.7)
    assert feats["wallet_quality"] == 0.9
    assert prov["wallet_quality"]["source"] == "override"
    assert feats["velocity"] == 0.7


# ── End-to-end endpoint ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_token_from_intel(client, fresh_agent):
    from backend.db import DB_PATH
    from backend.routers.alpha import _SOCIAL_SIGNALS_DDL

    agent = await fresh_agent()
    headers = {"X-Agent-Key": agent["api_key"]}

    # Seed real social sentiment for a ticker.
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_SOCIAL_SIGNALS_DDL)
        await db.execute(
            "INSERT INTO social_signals (platform, username, ticker, sentiment, confidence, signal_type, post_text) "
            "VALUES (?,?,?,?,?,?,?)",
            ("twitter", "alpha_caller", "WAGMI", "BULLISH", 0.8, "call", "sending it $WAGMI"),
        )
        await db.commit()

    r = await client.get("/api/alpha/token/WAGMI", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "WAGMI"
    assert body["signal_count"] >= 1
    assert body["provenance"]["social"]["source"] == "social_signals+pool"
    assert body["inputs"]["social"] > 0.5           # bullish call lifted social
    assert "mandelbrot" in body and "grade" in body


@pytest.mark.asyncio
async def test_token_endpoint_override(client, fresh_agent):
    agent = await fresh_agent()
    headers = {"X-Agent-Key": agent["api_key"]}
    r = await client.get(
        "/api/alpha/token/NODATA",
        params={"wallet_quality": 0.9, "concentration": 0.05, "security": 0.9},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inputs"]["wallet_quality"] == 0.9
    assert body["provenance"]["wallet_quality"]["source"] == "override"
