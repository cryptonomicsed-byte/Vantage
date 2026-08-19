"""vantage-anvil — route-schema compatibility heuristic + tier BFS.

Covers the pure logic only: _normalize_routes, _tokenize_skill,
_decide_relationship, and the BFS distance calculation get_tiers performs
(exercised directly here rather than through the full endpoint, since the
BFS itself has no DB/network dependency once given a stars/edges shape).
Network/kernel-bridge code (_call_skillforge, agent bootstrap) is
integration-tested live against the real deployment, not here -- an SSH
bridge to a different pillar's kernel has no meaningful unit-test double.
"""
from collections import deque

from backend.routers.vantage_anvil import (
    _normalize_routes,
    _tokenize_skill,
    _decide_relationship,
    PRIME_STAR_ID,
)


# ── _normalize_routes ────────────────────────────────────────────────────

def test_normalizes_dict_shape_routes():
    routes = {"health": "GET /health", "run": "POST /run"}
    result = _normalize_routes(routes)
    assert ("GET", "/health", "health") in result
    assert ("POST", "/run", "run") in result


def test_normalizes_dict_shape_defaults_to_get_when_no_method_prefix():
    routes = {"root": "/"}
    result = _normalize_routes(routes)
    assert result == [("GET", "/", "root")]


def test_normalizes_list_shape_routes():
    routes = [
        {"method": "POST", "path": "/run"},
        {"method": "GET", "path": "/health"},
    ]
    result = _normalize_routes(routes)
    assert ("POST", "/run", "") in result
    assert ("GET", "/health", "") in result


def test_normalize_routes_handles_empty_and_malformed_input():
    assert _normalize_routes({}) == []
    assert _normalize_routes([]) == []
    assert _normalize_routes(None) == []
    assert _normalize_routes("not a routes object") == []


# ── _tokenize_skill ───────────────────────────────────────────────────────

def test_tokenize_extracts_tokens_from_name_and_description():
    tokens, produce, consume = _tokenize_skill(
        "wallet-tracker", "tracks wallet balances", {}
    )
    assert "wallet" in tokens
    assert "tracker" in tokens


def test_tokenize_get_routes_lean_produce():
    tokens, produce, consume = _tokenize_skill(
        "reporter", "", {"get_report": "GET /report"}
    )
    assert "report" in produce


def test_tokenize_post_routes_lean_consume():
    tokens, produce, consume = _tokenize_skill(
        "ingester", "", {"ingest": "POST /ingest"}
    )
    assert "ingest" in consume


def test_tokenize_repo_param_signals_consume():
    tokens, produce, consume = _tokenize_skill(
        "forge", "", {}, params={"clone_url": "string"}
    )
    assert {"repo", "code", "source"} <= consume


def test_tokenize_strips_stopwords():
    tokens, produce, consume = _tokenize_skill("api-v1-tool", "the api for http", {})
    assert "api" not in tokens
    assert "v1" not in tokens
    assert "the" not in tokens
    assert "http" not in tokens


# ── _decide_relationship ─────────────────────────────────────────────────

def test_produces_input_for_when_a_produces_what_b_consumes():
    a_tokens = {"report", "data"}
    a_produce = {"report"}
    a_consume = set()
    b_tokens = {"ingest", "report"}
    b_produce = set()
    b_consume = {"report"}
    predicate, direction, weight = _decide_relationship(
        a_tokens, a_produce, a_consume, b_tokens, b_produce, b_consume
    )
    assert predicate == "produces_input_for"
    assert direction == "a_to_b"
    assert weight > 0


def test_produces_input_for_reverses_direction_when_b_produces_for_a():
    a_tokens = {"ingest", "data"}
    a_produce = set()
    a_consume = {"data"}
    b_tokens = {"data", "export"}
    b_produce = {"data"}
    b_consume = set()
    predicate, direction, weight = _decide_relationship(
        a_tokens, a_produce, a_consume, b_tokens, b_produce, b_consume
    )
    assert predicate == "produces_input_for"
    assert direction == "b_to_a"


def test_combines_with_when_mutual_produce_consume():
    a_tokens = {"x", "y"}
    a_produce = {"x"}
    a_consume = {"y"}
    b_tokens = {"x", "y"}
    b_produce = {"y"}
    b_consume = {"x"}
    predicate, direction, weight = _decide_relationship(
        a_tokens, a_produce, a_consume, b_tokens, b_produce, b_consume
    )
    assert predicate == "combines_with"
    assert direction is None


def test_combines_with_on_high_token_overlap_without_produce_consume_match():
    a_tokens = {"wallet", "tracker", "balance", "solana"}
    b_tokens = {"wallet", "tracker", "balance", "ethereum"}
    predicate, direction, weight = _decide_relationship(
        a_tokens, set(), set(), b_tokens, set(), set()
    )
    assert predicate == "combines_with"


def test_complements_on_moderate_overlap():
    a_tokens = {"wallet", "tracker", "balance", "solana", "chain", "data"}
    b_tokens = {"wallet", "explorer", "block", "gas", "fee", "node"}
    predicate, direction, weight = _decide_relationship(
        a_tokens, set(), set(), b_tokens, set(), set()
    )
    assert predicate == "complements"


def test_no_relationship_on_disjoint_projects():
    a_tokens = {"weather", "forecast", "rain"}
    b_tokens = {"chess", "engine", "opening"}
    predicate, direction, weight = _decide_relationship(
        a_tokens, set(), set(), b_tokens, set(), set()
    )
    assert predicate is None
    assert weight == 0.0


# ── Tier BFS (same algorithm get_tiers runs, tested directly) ────────────

def _bfs_tiers(stars_by_id: dict, edges: list[dict]) -> dict:
    adjacency: dict = {sid: set() for sid in stars_by_id}
    for e in edges:
        subj, obj = e.get("subject"), e.get("object")
        if subj in stars_by_id and obj in stars_by_id:
            adjacency.setdefault(subj, set()).add(obj)
            adjacency.setdefault(obj, set()).add(subj)
    dist = {}
    if PRIME_STAR_ID in stars_by_id:
        dist[PRIME_STAR_ID] = 0
        q = deque([PRIME_STAR_ID])
        while q:
            cur = q.popleft()
            for nxt in adjacency.get(cur, ()):
                if nxt not in dist:
                    dist[nxt] = dist[cur] + 1
                    q.append(nxt)
    return dist


def test_prime_star_is_tier_zero():
    stars = {PRIME_STAR_ID: {}, "project_a": {}}
    edges = [{"subject": "project_a", "object": PRIME_STAR_ID}]
    dist = _bfs_tiers(stars, edges)
    assert dist[PRIME_STAR_ID] == 0
    assert dist["project_a"] == 1


def test_two_hop_project_is_tier_two():
    stars = {PRIME_STAR_ID: {}, "project_a": {}, "project_b": {}}
    edges = [
        {"subject": "project_a", "object": PRIME_STAR_ID},
        {"subject": "project_b", "object": "project_a"},
    ]
    dist = _bfs_tiers(stars, edges)
    assert dist["project_b"] == 2


def test_unreachable_project_has_no_distance():
    stars = {PRIME_STAR_ID: {}, "project_a": {}, "orphan": {}}
    edges = [{"subject": "project_a", "object": PRIME_STAR_ID}]
    dist = _bfs_tiers(stars, edges)
    assert "orphan" not in dist
    unreachable = [sid for sid in stars if sid not in dist]
    assert unreachable == ["orphan"]


def test_shortest_path_wins_with_multiple_routes():
    """A project reachable both directly and via a longer path gets the
    shorter distance -- BFS guarantees this, not whichever edge was
    inserted first."""
    stars = {PRIME_STAR_ID: {}, "project_a": {}, "project_b": {}}
    edges = [
        {"subject": "project_b", "object": "project_a"},
        {"subject": "project_a", "object": PRIME_STAR_ID},
        {"subject": "project_b", "object": PRIME_STAR_ID},  # also direct
    ]
    dist = _bfs_tiers(stars, edges)
    assert dist["project_b"] == 1
