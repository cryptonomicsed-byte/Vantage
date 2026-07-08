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
