"""Tests for backend/influencer_correlation.py -- real multi-influencer
coordinated-mention detection over social_signals. Real-world fixture
values (accounts, contract address, exact timestamps) are taken directly
from production social_signals data (2026-08-30): dontcallmecallss and
pumpfunearlytrending both mentioned contract address
FC8D5Hs59Dx8dJpxdYjqcosk5XNcLaSqFMGqxaGgpump 19 seconds apart on
2026-08-23, a genuine coordinated-mention case this module is meant to
catch.
"""
import json

import aiosqlite
import pytest

from backend import influencer_correlation as ic
from backend import mycelium_bridge as mb


def _row(username, platform="telegram", ticker="", ca="", created_at="2026-08-23 22:14:22"):
    return {"platform": platform, "username": username, "ticker": ticker,
            "contract_address": ca, "created_at": created_at}


@pytest.fixture(autouse=True)
def _clear_dedup_state():
    ic._last_emitted.clear()
    yield
    ic._last_emitted.clear()


# ── find_coordinated_mentions ───────────────────────────────────────────

def test_real_production_case_two_accounts_19_seconds_apart():
    ca = "FC8D5Hs59Dx8dJpxdYjqcosk5XNcLaSqFMGqxaGgpump"
    rows = [
        _row("dontcallmecallss", ca=ca, created_at="2026-08-23 22:14:22"),
        _row("pumpfunearlytrending", ca=ca, created_at="2026-08-23 22:14:41"),
    ]
    findings = ic.find_coordinated_mentions(rows)
    assert len(findings) == 1
    f = findings[0]
    assert f["identifier"] == ca
    assert f["accounts"] == ["dontcallmecallss", "pumpfunearlytrending"]
    assert f["n_accounts"] == 2
    assert f["span_minutes"] < 1


def test_single_account_mentioning_twice_is_not_coordinated():
    rows = [
        _row("only-account", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("only-account", ticker="FOO", created_at="2026-08-23 10:05:00"),
    ]
    assert ic.find_coordinated_mentions(rows) == []


def test_two_accounts_outside_the_window_is_not_coordinated():
    rows = [
        _row("acct-a", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ticker="FOO", created_at="2026-08-23 12:30:00"),  # 150 min later
    ]
    assert ic.find_coordinated_mentions(rows, window_minutes=60) == []


def test_two_accounts_just_inside_the_window_is_coordinated():
    rows = [
        _row("acct-a", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ticker="FOO", created_at="2026-08-23 10:59:00"),
    ]
    findings = ic.find_coordinated_mentions(rows, window_minutes=60)
    assert len(findings) == 1
    assert findings[0]["n_accounts"] == 2


def test_ticker_and_contract_address_are_different_identifiers():
    # Same symbol text, but a bare $TICKER mention and a real CA mention
    # must NOT be conflated -- different identifier namespaces.
    rows = [
        _row("acct-a", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ca="SomeRealMintAddressXXXXXXXXXXXXXXXXXXXXXXXX", created_at="2026-08-23 10:01:00"),
    ]
    assert ic.find_coordinated_mentions(rows) == []


def test_contract_address_preferred_over_ticker_when_both_present():
    rows = [
        _row("acct-a", ticker="FOO", ca="RealMintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXX", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ticker="FOO", ca="RealMintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXX", created_at="2026-08-23 10:05:00"),
    ]
    findings = ic.find_coordinated_mentions(rows)
    assert len(findings) == 1
    assert findings[0]["identifier"] == "RealMintAddressXXXXXXXXXXXXXXXXXXXXXXXXXXXX"


def test_three_accounts_forms_one_finding_not_three():
    rows = [
        _row("acct-a", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ticker="FOO", created_at="2026-08-23 10:10:00"),
        _row("acct-c", ticker="FOO", created_at="2026-08-23 10:20:00"),
    ]
    findings = ic.find_coordinated_mentions(rows)
    assert len(findings) == 1
    assert findings[0]["n_accounts"] == 3
    assert findings[0]["accounts"] == ["acct-a", "acct-b", "acct-c"]


def test_unrelated_identifiers_produce_independent_findings():
    rows = [
        _row("acct-a", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ticker="FOO", created_at="2026-08-23 10:05:00"),
        _row("acct-a", ticker="BAR", created_at="2026-08-23 11:00:00"),
        _row("acct-c", ticker="BAR", created_at="2026-08-23 11:05:00"),
    ]
    findings = ic.find_coordinated_mentions(rows)
    assert len(findings) == 2
    idents = {f["identifier"] for f in findings}
    assert idents == {"ticker:FOO", "ticker:BAR"}


def test_min_accounts_threshold_is_respected():
    rows = [
        _row("acct-a", ticker="FOO", created_at="2026-08-23 10:00:00"),
        _row("acct-b", ticker="FOO", created_at="2026-08-23 10:05:00"),
    ]
    assert ic.find_coordinated_mentions(rows, min_accounts=3) == []


def test_rows_missing_username_or_identifier_are_skipped():
    rows = [
        {"platform": "telegram", "username": "", "ticker": "FOO", "contract_address": "", "created_at": "2026-08-23 10:00:00"},
        {"platform": "telegram", "username": "acct-b", "ticker": "", "contract_address": "", "created_at": "2026-08-23 10:00:00"},
    ]
    assert ic.find_coordinated_mentions(rows) == []


def test_malformed_timestamp_is_skipped_not_raised():
    rows = [
        _row("acct-a", ticker="FOO", created_at="not-a-real-timestamp"),
        _row("acct-b", ticker="FOO", created_at="2026-08-23 10:00:00"),
    ]
    assert ic.find_coordinated_mentions(rows) == []  # only one valid row left


def test_empty_rows_returns_no_findings():
    assert ic.find_coordinated_mentions([]) == []


# ── _emit / dedup ────────────────────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    calls = []

    class _Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        calls.append({"url": request.full_url, "body": json.loads(request.data.decode())})
        return _Response()

    monkeypatch.setattr(mb.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_emit_sends_a_real_trace_with_the_full_finding_as_payload(sent):
    finding = {
        "identifier": "ticker:FOO", "symbol": "FOO", "accounts": ["acct-a", "acct-b"],
        "platforms": ["telegram"], "n_accounts": 2, "first_seen": "2026-08-23 10:00:00",
        "last_seen": "2026-08-23 10:05:00", "span_minutes": 5.0,
    }
    assert ic._emit(finding) is True
    assert len(sent) == 1
    body = sent[0]["body"]
    assert body["agent"] == "influencer_correlation"
    assert body["kind"] == "observation"
    assert body["action"] == "coordinated_mention"
    assert body["target"] == "ticker:FOO"
    assert body["payload"] == finding


def test_unchanged_account_set_is_not_re_emitted(sent):
    finding = {"identifier": "ticker:FOO", "accounts": ["acct-a", "acct-b"], "n_accounts": 2,
               "symbol": "FOO", "platforms": [], "first_seen": "t1", "last_seen": "t2", "span_minutes": 1.0}
    ic._emit(finding)
    assert ic._emit(finding) is False
    assert len(sent) == 1


def test_a_new_account_joining_triggers_re_emission(sent):
    finding1 = {"identifier": "ticker:FOO", "accounts": ["acct-a", "acct-b"], "n_accounts": 2,
                "symbol": "FOO", "platforms": [], "first_seen": "t1", "last_seen": "t2", "span_minutes": 1.0}
    finding2 = {**finding1, "accounts": ["acct-a", "acct-b", "acct-c"], "n_accounts": 3}
    ic._emit(finding1)
    assert ic._emit(finding2) is True
    assert len(sent) == 2


# ── scan_and_emit (real DB integration) ─────────────────────────────────

@pytest.fixture
async def social_signals_db(tmp_path):
    path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(path))
    await conn.execute("""CREATE TABLE social_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, username TEXT,
        ticker TEXT, contract_address TEXT, created_at TEXT)""")
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_scan_and_emit_finds_real_recent_coordination(social_signals_db, sent):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    t2 = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    await social_signals_db.execute(
        "INSERT INTO social_signals (platform, username, ticker, contract_address, created_at) VALUES "
        "('telegram','acct-a','FOO','', ?), ('telegram','acct-b','FOO','', ?)",
        (t1, t2),
    )
    await social_signals_db.commit()

    findings = await ic.scan_and_emit(social_signals_db)
    assert len(findings) == 1
    assert findings[0]["identifier"] == "ticker:FOO"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_scan_and_emit_ignores_mentions_outside_the_lookback_window(social_signals_db, sent):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=ic.LOOKBACK_MINUTES + 60)).strftime("%Y-%m-%d %H:%M:%S")
    await social_signals_db.execute(
        "INSERT INTO social_signals (platform, username, ticker, contract_address, created_at) VALUES "
        "('telegram','acct-a','FOO','', ?), ('telegram','acct-b','FOO','', ?)",
        (old, old),
    )
    await social_signals_db.commit()

    findings = await ic.scan_and_emit(social_signals_db)
    assert findings == []
    assert sent == []
