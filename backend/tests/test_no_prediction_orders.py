"""The prediction pipeline must never be able to create a trading order.

This is a static test on purpose. A runtime test can only show that a call
failed; it cannot show that the code path is gone, and this pipeline once
contained a live POST to /api/trading/signals/ingest -- the order-creating
endpoint -- hidden behind a bare `except` that swallowed its 401. It looked
inert and was one payload field away from being armed.

Two invariants:

1. No prediction-market daemon references an order-creating surface.
2. The guard cannot be dodged by adding a new daemon: any daemons/*.py whose
   name looks prediction-related must be in the checked list below, so a new
   file fails this test until someone decides which side of the line it is on.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DAEMONS_DIR = Path(__file__).resolve().parents[2] / "daemons"

# Surfaces that turn a decision into a real order.
ORDER_SURFACES = (
    "/api/trading/signals/ingest",
    "create_order",
    "place_order",
    "/api/trading/orders",
)

# Daemons that own the prediction pipeline: read venues, decide, record.
PREDICTION_DAEMONS = {
    "prediction_market_signal_daemon.py",
    "brier_calibration_gate_daemon.py",
    "prediction_context_daemon.py",
    "kalshi_collector_daemon.py",
    "polymarket_collector_daemon.py",
    # Feeds observation-grade prediction signals into /api/intel/signals/ingest,
    # which persists to signal_pool and creates nothing. Not an order surface,
    # but it is prediction data, so it belongs on the guarded side of the line.
    "polymarket_bridge.py",
}

# Trading engines for whom placing orders is the job. Excluded from the scan by
# name, not by pattern: "predict" in a filename is not a claim about what it
# does. If one of these ever owns prediction-market decisions, move it into
# PREDICTION_DAEMONS instead of adding another exemption.
TRADING_ENGINES = {
    "vantage_predictor.py",   # 8-indicator TA consensus -> signals/ingest by design
    "polymarket_trader.py",   # executes Polymarket orders by design
}

PREDICTION_NAME = re.compile(r"(prediction|brier|kalshi|polymarket|context)", re.IGNORECASE)


def _sources(names: set[str]) -> dict[str, str]:
    """Source with whole-line comments stripped.

    The daemon that used to hold the offending call now carries a comment
    explaining why it was removed, and that comment quotes the endpoint. A guard
    that trips on its own documentation teaches people to delete the
    documentation.
    """
    out = {}
    for name in names:
        text = (DAEMONS_DIR / name).read_text(encoding="utf-8")
        out[name] = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
    return out


def test_no_prediction_daemon_targets_an_order_surface():
    offenders = []
    for name, text in _sources(PREDICTION_DAEMONS).items():
        for surface in ORDER_SURFACES:
            if surface in text and name not in TRADING_ENGINES:
                offenders.append(f"{name} -> {surface}")
    assert not offenders, (
        "prediction daemons must not target order-creating endpoints: "
        + ", ".join(offenders)
    )


def test_guard_covers_every_prediction_named_daemon():
    """A new prediction-named daemon must be classified, not silently exempt."""
    present = {p.name for p in DAEMONS_DIR.glob("*.py")}
    unclassified = sorted(
        n for n in present
        if PREDICTION_NAME.search(n)
        and n not in PREDICTION_DAEMONS
        and n not in TRADING_ENGINES
    )
    assert not unclassified, (
        "prediction-named daemons missing from the order-path guard: "
        + ", ".join(unclassified)
        + " — add each to PREDICTION_DAEMONS or TRADING_ENGINES in "
          "tests/test_no_prediction_orders.py"
    )


def test_the_scan_actually_finds_the_endpoint_it_guards():
    """Negative control: if the string disappeared from the codebase entirely,
    the first test would pass vacuously. The trading router still defines it."""
    trading = (Path(__file__).resolve().parents[2] / "backend/routers/trading.py").read_text(encoding="utf-8")
    assert "conviction > 0.7" in trading
    assert '("/signals/ingest")' in trading or "/signals/ingest" in trading
