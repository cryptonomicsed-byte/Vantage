"""Unit tests for Living Manifesto consensus math (mirrors ifascript)."""
from backend.manifesto_store import (
    clause_proposed_event,
    clause_ratified_event,
    level_for_weight,
    vote_weight,
)


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


def test_clause_proposed_event_shape():
    # Field names + type discriminator must match omo-koda2's
    # ManifestoClauseProposed JSON so the mesh speaks one vocabulary.
    ev = clause_proposed_event("guild", 7, 42, "Oracle", "speak truth", "luna")
    assert ev == {
        "type": "manifesto_clause_proposed",
        "collective": "guild",
        "clause_id": 7,
        "odu_id": 42,
        "vessel": "Oracle",
        "principle": "speak truth",
        "author": "luna",
    }


def test_clause_ratified_event_shape():
    ev = clause_ratified_event("guild", 7, "council", 5.0)
    assert ev == {
        "type": "manifesto_clause_ratified",
        "collective": "guild",
        "clause_id": 7,
        "level": "council",
        "weight": 5.0,
    }
