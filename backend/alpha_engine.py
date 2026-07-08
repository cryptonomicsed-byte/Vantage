"""
Pre-migration alpha engine — the filtering brain of the pump.fun sniper.

Two pure, deterministic primitives (no I/O, so they are trivially testable and
verifiable, and they mirror the AXIOM Fractal Oracle's semantics exactly):

  • mandelbrot_robustness(...) maps a token's early metrics to a point c in the
    Mandelbrot plane and iterates z→z²+c. A **bounded** orbit means stable,
    survivable momentum (a likely graduate); a **fast escape** means rug/dump
    fragility. Healthy, balanced metrics land inside the main cardioid; extreme
    concentration / weak security / blow-off velocity push c outside the set.

  • composite_alpha_score(...) combines the weighted feature score
    (wallet 40% · velocity 25% · concentration 15% · social 10% · security 10%)
    with the Mandelbrot robustness as a meta-gate, so a brittle token can never
    surface on feature score alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


def _escape_time(cr: float, ci: float, maxiter: int = 200) -> int:
    """Iterations before |z|>2, or maxiter if the orbit stays bounded."""
    zr = zi = 0.0
    for i in range(maxiter):
        zr2 = zr * zr
        zi2 = zi * zi
        if zr2 + zi2 > 4.0:
            return i
        zi = 2.0 * zr * zi + ci
        zr = zr2 - zi2 + cr
    return maxiter


@dataclass
class Robustness:
    bounded: bool
    escape: int
    maxiter: int
    stability: float          # 0..1, 1.0 = fully bounded (robust island)
    risk: float               # 0..1, fragility toward rug/dump
    verdict: str              # robust momentum | fragile boundary | escape zone
    c: tuple                  # the mapped (re, im)

    def as_dict(self) -> dict:
        return {
            "bounded": self.bounded,
            "escape": self.escape,
            "maxiter": self.maxiter,
            "stability": round(self.stability, 4),
            "risk": round(self.risk, 4),
            "verdict": self.verdict,
            "c": [round(self.c[0], 4), round(self.c[1], 4)],
        }


def mandelbrot_robustness(
    velocity: float,
    concentration: float,
    security: float,
    social: float = 0.5,
    maxiter: int = 200,
) -> Robustness:
    """Map early-curve risk into the Mandelbrot plane.

    Inputs are 0..1. `concentration` (holder concentration) and low `security`
    raise fragility; only *blow-off* velocity (>0.8) adds risk — healthy
    momentum does not. The mapping places a clean token near (-0.5, 0) (deep
    inside the cardioid → bounded) and a rug-shaped token out past the boundary.
    """
    velocity = _clamp01(velocity)
    concentration = _clamp01(concentration)
    security = _clamp01(security)

    blowoff = max(0.0, velocity - 0.8) / 0.2                 # 0 until >0.8, then ramps
    risk_drivers = 0.45 * concentration + 0.35 * (1.0 - security) + 0.20 * blowoff
    risk_drivers = _clamp01(risk_drivers)

    # Map risk along the real slice of the Mandelbrot set, where escape is
    # MONOTONIC: c is bounded for cr ≤ 0.25 (the cardioid cusp) and diverges
    # beyond it, escaping faster the larger cr grows. risk→0 sits deep inside
    # at cr=-0.5 (robust); risk≈0.5 hits the boundary; risk→1 escapes fast.
    # (A 2-D diagonal would cross period bulbs and break monotonicity.)
    cr = -0.5 + risk_drivers * 1.5
    ci = 0.0

    escape = _escape_time(cr, ci, maxiter)
    bounded = escape >= maxiter
    stability = escape / maxiter
    risk = 1.0 - stability
    if bounded:
        verdict = "robust momentum"
    elif stability > 0.5:
        verdict = "fragile boundary"
    else:
        verdict = "escape zone"
    return Robustness(bounded, escape, maxiter, stability, risk, verdict, (cr, ci))


# Composite weights (must sum to 1.0). concentration is inverted (low is good).
WEIGHTS = {
    "wallet_quality": 0.40,
    "velocity": 0.25,
    "concentration": 0.15,
    "social": 0.10,
    "security": 0.10,
}


def composite_alpha_score(
    wallet_quality: float,
    velocity: float,
    concentration: float,
    social: float,
    security: float,
) -> dict:
    """Composite pre-migration play-quality score in [0,1] with a breakdown.

    Feature score uses the weighting above (holder concentration inverted so
    that dispersed early holders score higher). The Mandelbrot robustness then
    gates the result: a fragile token keeps only ~half its feature score, so a
    high-feature but rug-shaped launch can never grade well.
    """
    wallet_quality = _clamp01(wallet_quality)
    velocity = _clamp01(velocity)
    concentration = _clamp01(concentration)
    social = _clamp01(social)
    security = _clamp01(security)

    contrib = {
        "wallet_quality": WEIGHTS["wallet_quality"] * wallet_quality,
        "velocity": WEIGHTS["velocity"] * velocity,
        "concentration": WEIGHTS["concentration"] * (1.0 - concentration),
        "social": WEIGHTS["social"] * social,
        "security": WEIGHTS["security"] * security,
    }
    feature_score = sum(contrib.values())

    robo = mandelbrot_robustness(velocity, concentration, security, social)
    gate = 0.5 + 0.5 * robo.stability          # robust → 1.0, escape → 0.5..
    score = _clamp01(feature_score * gate)

    if score >= 0.75:
        grade, action = "A", "surface"
    elif score >= 0.6:
        grade, action = "B", "surface"
    elif score >= 0.45:
        grade, action = "C", "watch"
    else:
        grade, action = "D", "skip"
    # A structurally fragile token is never auto-surfaced regardless of score.
    if robo.verdict == "escape zone" and action == "surface":
        action = "watch"

    return {
        "score": round(score, 4),
        "feature_score": round(feature_score, 4),
        "grade": grade,
        "recommendation": action,
        "breakdown": {k: round(v, 4) for k, v in contrib.items()},
        "weights": WEIGHTS,
        "mandelbrot": robo.as_dict(),
        "inputs": {
            "wallet_quality": wallet_quality, "velocity": velocity,
            "concentration": concentration, "social": social, "security": security,
        },
    }


# ── Feature assembly from live incoming intel ────────────────────────────────
# The scanner/wallet-profiler supplies hard on-chain axes (wallet_quality,
# concentration) directly. The soft, chatter-driven axes (social, security) are
# derived here from the real signals the daemons ingest: social_tracker's
# social_signals rows, the in-memory intel signal pool (TG/Twitter), and any
# pumpfun/poison chatter. These are pure functions over a normalised signal
# list so they stay trivially testable, exactly like the scorer above.

# A normalised signal is a dict with: source, type, direction, conviction (0..1),
# detail (str). Helpers below are tolerant of missing keys.

_BULLISH = {"BULLISH", "BUY", "LONG", "UP"}
_BEARISH = {"BEARISH", "SELL", "SHORT", "DOWN"}
_SOCIAL_TYPES = {"sentiment", "mention", "call", "alpha", "telegram_alpha",
                 "trending", "social", "trending_pool"}
_STRONG_SOCIAL = {"call", "alpha", "telegram_alpha"}
_RUG_WORDS = ("rug", "scam", "honeypot", "mint authority", "freeze authority",
              "poison", "insider", "bundl", "sniper", "dev sold", "dev dump")
_CONC_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _dir(sig: dict) -> str:
    d = str(sig.get("direction") or sig.get("sentiment") or "").upper()
    return d


def _conv(sig: dict) -> float:
    return _clamp01(float(sig.get("conviction", sig.get("confidence", 0.5) or 0.5)))


def derive_social(signals: list) -> tuple:
    """Social momentum 0..1 from chatter signals, weighted by conviction and
    signal strength (a 'call'/'alpha' counts harder than a bare mention).
    Bullish chatter lifts it, bearish chatter drags it. Neutral baseline 0.5."""
    social_sigs = [s for s in signals
                   if str(s.get("type", "")).lower() in _SOCIAL_TYPES
                   or "social" in str(s.get("source", "")).lower()
                   or "telegram" in str(s.get("source", "")).lower()]
    if not social_sigs:
        return 0.5, {"source": "none", "n": 0, "note": "no social signals"}
    pos = neg = 0.0
    for s in social_sigs:
        w = _conv(s) * (1.5 if str(s.get("type", "")).lower() in _STRONG_SOCIAL else 1.0)
        d = _dir(s)
        if d in _BULLISH:
            pos += w
        elif d in _BEARISH:
            neg += w
        else:
            pos += 0.25 * w  # neutral presence still signals attention
    social = _clamp01(0.45 + 0.10 * pos - 0.12 * neg)
    return social, {"source": "social_signals+pool", "n": len(social_sigs),
                    "bull_weight": round(pos, 3), "bear_weight": round(neg, 3)}


def derive_security(signals: list) -> tuple:
    """Security 0..1 (HIGHER = safer). Starts mildly-safe and is dragged down by
    rug/poison chatter and bearish structural warnings. A high-conviction poison
    alert collapses it toward zero."""
    hits = []
    drag = 0.0
    for s in signals:
        blob = f"{s.get('type','')} {s.get('detail','')}".lower()
        stype = str(s.get("type", "")).lower()
        conv = _conv(s)
        if stype in ("poison_alert", "poison") or "poison_radar" in str(s.get("source", "")):
            drag += 0.30 + 0.30 * conv
            hits.append(s)
        elif any(w in blob for w in _RUG_WORDS):
            drag += 0.12 + 0.18 * conv
            hits.append(s)
        elif _dir(s) in _BEARISH and ("concentrat" in blob or "holder" in blob):
            drag += 0.10 * conv
            hits.append(s)
    security = _clamp01(0.70 - drag)
    return security, {"source": "poison/rug chatter" if hits else "default",
                      "n": len(hits), "drag": round(drag, 3)}


def derive_concentration(signals: list) -> tuple:
    """Holder concentration 0..1 (LOWER = better) parsed from holder-intel
    chatter, e.g. 'Top 5 holders own 34%'. Falls back to neutral 0.5."""
    best = None
    for s in signals:
        blob = f"{s.get('detail','')}".lower()
        if "holder" in blob or "concentrat" in blob or "top 5" in blob or "top5" in blob:
            m = _CONC_PCT.search(blob)
            if m:
                pct = float(m.group(1)) / 100.0
                best = pct if best is None else max(best, pct)
    if best is None:
        return 0.5, {"source": "default", "note": "no holder intel"}
    return _clamp01(best), {"source": "holder chatter", "top_holders_pct": round(best, 3)}


def assemble_features(
    signals: list,
    overrides: Optional[dict] = None,
    velocity_hint: Optional[float] = None,
) -> tuple:
    """Assemble the 5 alpha features from live intel + caller-supplied overrides.

    `overrides` lets the scanner inject hard on-chain axes it already computed
    (wallet_quality from wallet backtracking, concentration from the holder map,
    an explicit velocity/security). Anything not overridden is derived from the
    real signal stream here. Returns (features, provenance)."""
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
    prov: dict = {}

    social, social_prov = derive_social(signals)
    security, sec_prov = derive_security(signals)
    concentration, conc_prov = derive_concentration(signals)

    features = {
        "wallet_quality": 0.5,
        "velocity": 0.5 if velocity_hint is None else _clamp01(velocity_hint),
        "concentration": concentration,
        "social": social,
        "security": security,
    }
    prov["social"] = social_prov
    prov["security"] = sec_prov
    prov["concentration"] = conc_prov
    prov["velocity"] = ({"source": "wallet_trades velocity"} if velocity_hint is not None
                        else {"source": "default", "note": "no velocity hint"})
    prov["wallet_quality"] = {"source": "default", "note": "supply via override"}

    for k in features:
        if k in overrides:
            features[k] = _clamp01(float(overrides[k]))
            prov[k] = {"source": "override"}

    return features, prov
