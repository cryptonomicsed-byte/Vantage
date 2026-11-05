"""Golden vectors for the odu cast port.

The first two tests replay the Rust crate's own assertions
(Bipon39-Rust/tests/ifascript.rs): if the port ever diverges from
bipon39::ifascript::odu_primary_index, these fail. No cargo needed.

Run: python3 -m pytest backend/tests/test_odu_cast.py -q
"""
import json
import os
import secrets

import pytest

from backend import odu_cast


# --- parity with Bipon39-Rust -------------------------------------------------

def test_replays_crate_golden_vector():
    # tests/ifascript.rs: odu_index_in_range
    words = ["esu-elegbara", "sango", "osun", "yemoja"]
    assert odu_cast.odu_primary_index(words) == 188


def test_replays_crate_xor_vector():
    # tests/ifascript.rs: odu_xor_correctness
    words = ["esu-elegbara", "sango", "osun"]
    assert odu_cast.odu_primary_index(words) == 0 ^ 88 ^ 108


def test_token_indices_match_flat_index_minus_one():
    tokens, index = odu_cast._load_wordlist()
    assert len(tokens) == 256
    assert index["esu-elegbara"] == 0
    assert index["sango"] == 88
    assert index["osun"] == 108
    assert index["yemoja"] == 136
    assert index[tokens[-1]] == 255


def test_odu_index_equals_xor_fold_of_entropy():
    # One byte == one word == one token index, so the XOR-reduce collapses to a
    # byte-wise XOR fold. This is the property the whole hook leans on.
    for _ in range(100):
        entropy = secrets.token_bytes(32)
        words = odu_cast.entropy_to_mnemonic(entropy)
        assert len(words) == 32
        assert odu_cast.odu_primary_index(words) == odu_cast.xor_fold(entropy)


def test_2048_mode_token_uses_base_shifted_subtone():
    assert odu_cast.token_index("sango~alpha") == 88 << 3
    assert odu_cast.token_index("sango~theta") == (88 << 3) | 7
    with pytest.raises(KeyError):
        odu_cast.token_index("sango~omega")


def test_unknown_token_rejected():
    with pytest.raises(KeyError):
        odu_cast.odu_primary_index(["not-a-bipon39-token"])


# --- cast ---------------------------------------------------------------------

def test_cast_is_deterministic():
    a = odu_cast.cast("vantage-eco", nonce="signal-42")
    b = odu_cast.cast("vantage-eco", nonce="signal-42")
    assert a.cast_id == b.cast_id
    assert a.primary.index == b.primary.index
    assert a.corroborating.index == b.corroborating.index


def test_cast_legs_differ_and_nonce_matters():
    a = odu_cast.cast("vantage-eco", nonce="signal-42")
    b = odu_cast.cast("vantage-eco", nonce="signal-43")
    assert a.cast_id != b.cast_id
    # Legs are independent derivations; equal signs are possible but should not
    # be the norm across a sample.
    same = sum(
        1
        for i in range(64)
        if (c := odu_cast.cast("seed", nonce=str(i))).primary.index
        == c.corroborating.index
    )
    assert same < 16


def test_cast_lands_in_table_and_renders_metadata():
    casting = odu_cast.cast("vantage-eco", nonce="abc")
    for leg in (casting.primary, casting.corroborating):
        assert 1 <= leg.index <= 256
        assert len(leg.binary) == 8
        assert set(leg.binary) <= {"0", "1"}
        assert leg.odu["sacred_name"]
    payload = casting.to_dict()
    assert json.dumps(payload)  # serialisable for a receipt


def test_hamming_and_principal_flags():
    casting = odu_cast.cast("seed", nonce="xyz")
    assert 0 <= casting.hamming <= 8
    assert casting.principal == (
        casting.primary.tier == 1 and casting.corroborating.tier == 1
    )


# --- gate ---------------------------------------------------------------------

@pytest.fixture
def agreeing_principal(monkeypatch):
    """Find a seed whose two legs agree and are both principal odus."""
    for i in range(4000):
        casting = odu_cast.cast("scan", nonce=str(i))
        if casting.principal and casting.hamming <= odu_cast.AGREE_MAX:
            return casting
    pytest.skip("no agreeing principal pair found in scan range")


@pytest.fixture
def contradicting(monkeypatch):
    for i in range(4000):
        casting = odu_cast.cast("scan", nonce=str(i))
        if casting.hamming >= odu_cast.CONFLICT_MIN:
            return casting
    pytest.skip("no contradicting pair found in scan range")


def test_gate_allows_principal_agreement_at_any_stake_below_hard_cap(agreeing_principal):
    verdict = odu_cast.gate(agreeing_principal, stake_usd=odu_cast.STAKE_HARD_CAP - 1)
    assert verdict.decision == "allow"
    assert verdict.blocked is False


def test_gate_escalates_principal_agreement_above_hard_cap(agreeing_principal):
    verdict = odu_cast.gate(agreeing_principal, stake_usd=odu_cast.STAKE_HARD_CAP + 1)
    assert verdict.decision == "escalate"


def test_gate_vetoes_contradiction_with_real_money_at_risk(contradicting):
    verdict = odu_cast.gate(contradicting, stake_usd=odu_cast.STAKE_SOFT_CAP * 2)
    assert verdict.decision == "veto"
    assert verdict.blocked is True


def test_gate_escalates_contradiction_at_low_stake(contradicting):
    verdict = odu_cast.gate(contradicting, stake_usd=1.0)
    assert verdict.decision == "escalate"


def test_gate_never_allows_when_hermetic_gate_is_strict(monkeypatch):
    strict = None
    for i in range(4000):
        casting = odu_cast.cast("strict", nonce=str(i))
        if casting.mean_gate >= odu_cast.GATE_STRICT_THRESHOLD:
            strict = casting
            break
    if strict is None:
        pytest.skip("no strict-gate pair in scan range")
    verdict = odu_cast.gate(strict, stake_usd=0.0)
    assert verdict.decision != "allow"


def test_gate_verdict_is_receipt_shaped(contradicting):
    verdict = odu_cast.gate(contradicting, stake_usd=500)
    payload = verdict.to_dict()
    for key in ("decision", "reason", "cast_id", "primary", "corroborating", "stake_usd"):
        assert key in payload
    json.dumps(payload)
