#!/usr/bin/env python3
"""Standalone launcher for trade_outcome_learner.outcome_learner_loop()."""
import sys, asyncio, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
sys.path.insert(0, '/opt/ares/Vantage')
from backend.trade_outcome_learner import outcome_learner_loop, LOOP_INTERVAL_S
if len(sys.argv) > 1:
    import backend.trade_outcome_learner as _m
    _m.LOOP_INTERVAL_S = int(sys.argv[1])
asyncio.run(outcome_learner_loop())
