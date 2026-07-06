"""Tests for runner._run_query — the message-stream result-extraction logic.

This is the heart of every run: drain the SDK message stream, prefer the
ResultMessage's text, fall back to assistant text when there's no result, and
turn an error result into a raised exception. We fake the SDK's `query` with a
canned async stream so no real model is called.

    cd agent && ./.venv/bin/python test_runner.py
"""
import asyncio
import os

os.environ.setdefault("AGENT_TOKEN", "t")

import runner
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


def _assistant(*texts):
    return AssistantMessage(content=[TextBlock(text=t) for t in texts], model="test")


def _result(result, is_error=False):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                         is_error=is_error, num_turns=1, session_id="s", result=result)


def _fake_query(messages):
    """Return a stand-in for runner.query that yields the given messages."""
    async def gen(*, prompt, options):
        for m in messages:
            yield m
    return gen


def _run():
    return asyncio.run(runner._run_query("p", object()))   # options unused by the fake


def test_result_message_text_is_preferred():
    runner.query = _fake_query([_assistant("thinking out loud"), _result("FINAL ANSWER")])
    assert _run() == "FINAL ANSWER"   # ResultMessage wins over assistant chatter


def test_falls_back_to_assistant_text_when_no_result():
    # ResultMessage with an empty result -> join the assistant text blocks instead.
    runner.query = _fake_query([_assistant("part one", "part two"), _result(None)])
    assert _run() == "part one\npart two"


def test_error_result_raises():
    runner.query = _fake_query([_result("model exploded", is_error=True)])
    try:
        _run()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "model exploded" in str(e)


def test_error_result_without_text_still_raises():
    runner.query = _fake_query([_result(None, is_error=True)])
    try:
        _run()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "agent run failed" in str(e)   # default message when no result text


def _flaky_query(fail_times, error="API Error: Overloaded", result="RECOVERED"):
    """query fake that errors the first `fail_times` calls, then succeeds."""
    calls = {"n": 0}
    async def gen(*, prompt, options):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            yield _result(error, is_error=True)
        else:
            yield _result(result)
    return gen, calls


def test_transient_error_is_retried():
    runner.RETRY_BASE_SECONDS = 0          # no real sleeping in tests
    runner.query, calls = _flaky_query(fail_times=1)
    assert _run() == "RECOVERED" and calls["n"] == 2


def test_transient_error_exhausts_attempts():
    runner.RETRY_BASE_SECONDS = 0
    runner.query, calls = _flaky_query(fail_times=99)
    try:
        _run()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Overloaded" in str(e)
    assert calls["n"] == runner.RETRY_ATTEMPTS   # tried the full budget

def test_non_transient_error_is_not_retried():
    runner.RETRY_BASE_SECONDS = 0
    runner.query, calls = _flaky_query(fail_times=99, error="model exploded")
    try:
        _run()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
    assert calls["n"] == 1                       # no retry for a real failure


def test_is_transient_error():
    assert runner.is_transient_error("API Error: Overloaded")
    assert runner.is_transient_error("connection reset by peer")
    assert runner.is_transient_error("Request timed out")
    assert not runner.is_transient_error("model exploded")
    assert not runner.is_transient_error("agent run failed")
    assert not runner.is_transient_error(None)


def test_list_files_recurses_and_caps(tmp_path=None):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        sandbox = Path(d)
        (sandbox / "inputs.json").write_text("{}")
        (sandbox / "report").mkdir()
        (sandbox / "report" / "index.html").write_text("x")   # nested deliverable must be listed
        files = runner.list_files(sandbox)
        assert files == ["inputs.json", "report/index.html"]
        # cap: long tail is truncated with a count marker
        for i in range(5):
            (sandbox / f"f{i}.txt").write_text("x")
        files = runner.list_files(sandbox, cap=3)
        assert len(files) == 4 and files[-1] == "… (+4 more)"


def test_filter_handover_inputs():
    spec = '[{"key":"lang","label":"Language","type":"text"},{"key":"tone","type":"text"}]'
    kept, dropped = runner.filter_handover_inputs(spec, {"lang": "de", "typo": "x"})
    assert kept == {"lang": "de"} and dropped == ["typo"]
    # next workflow declares no inputs -> everything is undeclared
    kept, dropped = runner.filter_handover_inputs("[]", {"a": 1})
    assert kept == {} and dropped == ["a"]
    # unparseable spec -> no contract to enforce, keep as-is
    kept, dropped = runner.filter_handover_inputs("not json", {"a": 1})
    assert kept == {"a": 1} and dropped == []


if __name__ == "__main__":
    _orig = runner.query
    try:
        test_result_message_text_is_preferred()
        test_falls_back_to_assistant_text_when_no_result()
        test_error_result_raises()
        test_error_result_without_text_still_raises()
        test_transient_error_is_retried()
        test_transient_error_exhausts_attempts()
        test_non_transient_error_is_not_retried()
        test_is_transient_error()
        test_list_files_recurses_and_caps()
        test_filter_handover_inputs()
    finally:
        runner.query = _orig
    print("OK")
