"""Unit tests for Living Manifesto consensus math (mirrors ifascript)."""
from backend.manifesto_store import level_for_weight, vote_weight


def test_vote_weight_by_tier():
    assert vote_weight(1) == 1.0
    assert vote_weight(2) == 0.5
    # Tier 0 is guarded against division by zero.
    assert vote_weight(0) == 1.0


def test_level_thresholds_match_rust():
    assert level_for_weight(0.0) == "individual"
    assert level_for_weight(1.9) == "individual"
    assert level_for_weight(2.0) == "swarm"
    assert level_for_weight(4.9) == "swarm"
    assert level_for_weight(5.0) == "council"
    assert level_for_weight(9.9) == "council"
    assert level_for_weight(10.0) == "canonical"


def test_promotion_ladder():
    # Five tier-1 votes (weight 1.0 each) reach Council — enters canon.
    weight = sum(vote_weight(1) for _ in range(5))
    assert level_for_weight(weight) == "council"
