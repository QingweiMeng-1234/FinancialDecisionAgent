"""Offline contract tests for the installed Codex 0.149.0 app-server protocol."""
from collections import deque
import json

import pytest

from event_collector.theme_chokepoint import codex_session as cs


class FakeWire:
    def __init__(self, *, account="chatgpt", exhausted=False, events=()):
        self.account = account
        self.exhausted = exhausted
        self.events = deque(events)
        self.calls = []

    def request(self, method, params, *, timeout=10):
        self.calls.append((method, params))
        if method == "account/read":
            return {"account": {"type": self.account}}
        if method == "account/rateLimits/read":
            return {"rateLimits": {"primary": {"usedPercent": 100 if self.exhausted else 0}}}
        if method in {"thread/start", "thread/resume", "thread/unarchive"}:
            return {"thread": {"id": params.get("threadId", "thread-1"), "modelProvider": "openai"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        return {}

    def receive(self, *, timeout):
        if not self.events:
            raise TimeoutError("fixture event timeout")
        return self.events.popleft()

    def close(self):
        self.calls.append(("close", {}))


def usage(total, *, thread="thread-1", turn="turn-1"):
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread, "turnId": turn,
        "tokenUsage": {"total": {"totalTokens": total}}}}


def completed(*, status="completed", error=None, phase="final_answer"):
    return {"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {
        "id": "turn-1", "status": status, "error": error,
        "items": [{"type": "agentMessage", "phase": phase, "text": '{"candidates":[]}'}]}}}


def test_subscription_session_is_read_only_persistent_and_does_not_choose_a_model(tmp_path):
    wire = FakeWire()
    session = cs.CodexSession(wire, cwd=tmp_path)
    assert session.open() == "thread-1"
    params = next(p for m, p in wire.calls if m == "thread/start")
    assert params["sandbox"] == "read-only"
    assert params["ephemeral"] is False
    assert "model" not in params
    assert params["approvalPolicy"] == "never"
    session.open("thread-1")
    assert [m for m, _ in wire.calls].count("thread/start") == 1
    assert [m for m, _ in wire.calls].count("thread/resume") == 1


def test_turn_uses_own_cumulative_usage_and_filters_other_threads(tmp_path):
    wire = FakeWire(events=[usage(999, thread="other"), usage(12), usage(12), completed()])
    session = cs.CodexSession(wire, cwd=tmp_path)
    seen = []
    result = session.research("thread-1", prompt="research", schema={},
        timeout=1, token_limit=100, on_started=lambda t: seen.append(t),
        on_usage=lambda n: seen.append(n))
    assert result.payload == {"candidates": []}
    assert result.total_tokens == 12
    assert seen[0] == "turn-1"
    assert 999 not in seen
    assert all(m not in {"thread/delete", "thread/compact/start"} for m, _ in wire.calls)


@pytest.mark.parametrize("account", ["apiKey", None])
def test_api_key_or_missing_auth_never_falls_back_or_starts_session(tmp_path, account):
    wire = FakeWire(account=account)
    with pytest.raises(cs.CodexSessionError, match="CHATGPT_LOGIN_REQUIRED"):
        cs.CodexSession(wire, cwd=tmp_path).open()
    assert not any(m == "thread/start" for m, _ in wire.calls)


def test_account_exhaustion_pauses_before_a_turn_without_buying_credits(tmp_path):
    wire = FakeWire(exhausted=True)
    with pytest.raises(cs.CodexQuotaPaused):
        cs.CodexSession(wire, cwd=tmp_path).research("thread-1", prompt="x", schema={},
            timeout=1, token_limit=100, on_started=lambda _: None, on_usage=lambda _: None)
    assert not any(m == "turn/start" or "consume" in m or "login/start" in m for m, _ in wire.calls)


def test_soft_token_limit_interrupts_and_waits_for_terminal_ack(tmp_path):
    wire = FakeWire(events=[usage(101), completed(status="interrupted")])
    with pytest.raises(cs.CodexSessionError, match="TOKEN_LIMIT"):
        cs.CodexSession(wire, cwd=tmp_path).research("thread-1", prompt="x", schema={},
            timeout=1, token_limit=100, on_started=lambda _: None, on_usage=lambda _: None)
    assert sum(m == "turn/interrupt" for m, _ in wire.calls) == 1
    assert not wire.events


def test_archive_preserves_thread_and_unsubscribes_only_after_success(tmp_path):
    wire = FakeWire()
    cs.CodexSession(wire, cwd=tmp_path).archive("thread-1")
    assert [m for m, _ in wire.calls] == ["thread/archive", "thread/unsubscribe"]


def test_missing_token_usage_is_unknown_not_zero(tmp_path):
    wire = FakeWire(events=[completed()])
    result = cs.CodexSession(wire, cwd=tmp_path).research("thread-1", prompt="x", schema={},
        timeout=1, token_limit=100, on_started=lambda _: None, on_usage=lambda _: None)
    assert result.total_tokens is None


def test_no_final_answer_is_not_accepted_as_empty_research(tmp_path):
    wire = FakeWire(events=[usage(10), completed(phase="commentary")])
    with pytest.raises(cs.CodexSessionError, match="FINAL_OUTPUT_MISSING"):
        cs.CodexSession(wire, cwd=tmp_path).research("thread-1", prompt="x", schema={},
            timeout=1, token_limit=100, on_started=lambda _: None, on_usage=lambda _: None)
