"""Trace → engram bridge.

Two refusals get most of the attention, because both are cases where producing
something plausible would be worse than producing nothing:

  * a record with no timestamp is dropped, never stamped with the read time;
  * tool arguments and large results are described, never reproduced, because
    engrams are published and traces routinely carry things that must not be.
"""
import json

import pytest

from backend import trace_engram as te


def _lines(*records):
    return [json.dumps(r) for r in records]


# ── parsing ──────────────────────────────────────────────────────────────────

def test_well_formed_records_are_kept():
    lines = _lines(
        {"ts": 1.0, "tool": "tv_validate_pine_script", "args": {"code": "plot(close)"},
         "result": {"status": "ok"}, "duration_ms": 12.5},
        {"ts": 2.0, "tool": "tv_screenshot", "args": {}, "result": None, "duration_ms": 900},
    )
    assert len(list(te.parse_trace(lines))) == 2


def test_a_truncated_final_line_does_not_cost_the_session():
    """Append-only traces are routinely cut mid-write."""
    lines = _lines({"ts": 1.0, "tool": "a", "args": {}}) + ['{"ts": 2.0, "tool": "b"']
    assert len(list(te.parse_trace(lines))) == 1


def test_a_record_without_a_timestamp_is_dropped():
    """Never stamped with now(): that would turn a gap in the trace into a
    confident lie about when something happened."""
    lines = _lines({"tool": "a", "args": {}}, {"ts": "not-a-number", "tool": "b", "args": {}})
    assert list(te.parse_trace(lines)) == []


def test_a_record_without_a_tool_is_dropped():
    assert list(te.parse_trace(_lines({"ts": 1.0, "args": {}}))) == []
    assert list(te.parse_trace(_lines({"ts": 1.0, "tool": "", "args": {}}))) == []


def test_blank_lines_and_non_objects_are_skipped():
    assert list(te.parse_trace(["", "  ", "[1,2,3]", '"a string"'])) == []


# ── result summarization ─────────────────────────────────────────────────────

def test_a_large_result_is_described_not_reproduced():
    big = "x" * 5000
    out = te.summarize_result(big)
    assert out["truncated"] is True
    assert "value" not in out
    assert out["length"] == 5000


def test_a_small_text_result_is_kept():
    assert te.summarize_result("ok")["value"] == "ok"


def test_an_object_result_reports_keys_and_status():
    out = te.summarize_result({"status": "error", "detail": "boom", "secret": "s3cr3t"})
    assert out["status"] == "error"
    assert "detail" in out["keys"]
    # Key names travel; values do not.
    assert "s3cr3t" not in json.dumps(out)


def test_scalar_results_keep_their_values():
    assert te.summarize_result(42)["value"] == 42
    assert te.summarize_result(True)["value"] is True
    assert te.summarize_result(None)["type"] == "none"
    assert te.summarize_result([1, 2, 3])["length"] == 3


# ── engram content ───────────────────────────────────────────────────────────

def _records():
    return list(te.parse_trace(_lines(
        {"ts": 3.0, "tool": "b", "args": {"x": 1}, "result": {"status": "ok"}, "duration_ms": 10},
        {"ts": 1.0, "tool": "a", "args": {"code": "secret-source"},
         "result": {"status": "error"}, "duration_ms": 5},
        {"ts": 2.0, "tool": "b", "args": {}, "result": {"status": "ok"}, "duration_ms": 7.5},
    )))


def test_calls_are_ordered_by_timestamp():
    out = te.to_engram(_records(), agent="ares", session="s1")
    assert [c["ts"] for c in out["calls"]] == [1.0, 2.0, 3.0]


def test_argument_values_never_reach_the_engram():
    """The single most important property: engrams are published."""
    out = te.to_engram(_records(), agent="ares", session="s1")
    assert "secret-source" not in json.dumps(out)
    assert out["calls"][0]["arg_keys"] == ["code"]


def test_summary_counts_what_was_actually_read():
    out = te.to_engram(_records(), agent="ares", session="s1")["summary"]
    assert out["count"] == 3
    assert out["failures"] == 1
    assert out["total_ms"] == pytest.approx(22.5)
    assert out["tools"]["b"] == 2
    assert out["first_ts"] == 1.0 and out["last_ts"] == 3.0


def test_an_empty_trace_produces_an_empty_but_valid_engram():
    out = te.to_engram([], agent="ares", session="s1")
    assert out["summary"]["count"] == 0
    assert out["summary"]["first_ts"] is None
    assert out["calls"] == []


# ── addressing ───────────────────────────────────────────────────────────────

def test_slug_matches_the_minipae_grammar():
    assert te.engram_slug("Ares", "Session-1") == "mem/trace/ares/session-1"


def test_slug_folds_characters_the_grammar_rejects():
    slug = te.engram_slug("Ọ̀ṢỌ́VM", "run 3")
    assert slug is not None
    assert slug.startswith("mem/trace/")
    for part in slug[len("mem/"):].split("/"):
        assert all(c.isascii() and (c.islower() or c.isdigit() or c in "_-") for c in part), part


def test_slug_is_none_when_nothing_survives_normalization():
    """Fail here rather than build an address that only breaks at the relay."""
    assert te.engram_slug("", "s1") is None
    assert te.engram_slug("!!!", "s1") is None
    assert te.engram_slug("agent", "") is None


def test_from_file_reads_a_trace(tmp_path):
    p = tmp_path / "tool_calls.jsonl"
    p.write_text("\n".join(_lines(
        {"ts": 1.0, "tool": "a", "args": {}, "result": {"status": "ok"}, "duration_ms": 1},
    )), encoding="utf-8")
    out = te.from_file(str(p), agent="ares", session="s1")
    assert out["summary"]["count"] == 1
