"""D8 多轮追问 Agent 测试：mock LLM，不消耗真实 token."""

import json
from datetime import datetime, timezone

from repo_agent.agents.qa import QAIntent, RepoQA
from repo_agent.models import HealthReport, MetadataFinding, RepoMetadata, RepoRef


def _make_report() -> HealthReport:
    now = datetime.now(timezone.utc)
    meta = RepoMetadata(
        full_name="a/b",
        description="demo",
        language="Python",
        stars=100,
        forks=20,
        open_issues=5,
        created_at=now,
        updated_at=now,
        pushed_at=now,
        license_name="MIT",
        topics=["x"],
        has_issues=True,
        has_wiki=False,
        archived=False,
        homepage=None,
    )
    findings = [MetadataFinding("活跃度", 90, "良好", ["近 30 天有提交"], ["保持节奏"])]
    return HealthReport(
        repo=RepoRef("a", "b"), metadata=meta, findings=findings, summary="整体不错"
    )


class _FakeResp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def test_qa_parses_intent_and_answer(monkeypatch) -> None:
    import httpx

    payload = json.dumps(
        {"intent": "整体评估", "answer": "总体健康，值得投入。", "followup": "看下代码维度？"}
    )
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(payload))
    qa = RepoQA(_make_report())
    ex = qa.ask("这个仓库整体怎么样？")
    assert ex.intent == QAIntent.OVERALL
    assert "值得投入" in ex.answer
    assert ex.followup == "看下代码维度？"
    assert len(qa.history) == 1


def test_qa_context_tracking(monkeypatch) -> None:
    import httpx

    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        msgs = k["json"]["messages"]
        if calls["n"] == 2:
            assert any(m["role"] == "assistant" for m in msgs), "第二轮应携带历史上下文"
        return _FakeResp(json.dumps({"intent": "澄清追问", "answer": "好的", "followup": ""}))

    monkeypatch.setattr(httpx, "post", fake_post)
    qa = RepoQA(_make_report())
    qa.ask("代码维度怎么改？")
    qa.ask("那另一方面呢？")
    assert len(qa.history) == 2
    assert calls["n"] == 2


def test_qa_handles_bad_json(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp("这不是 json"))
    qa = RepoQA(_make_report())
    ex = qa.ask("随便问")
    assert ex.intent == QAIntent.OFF_TOPIC
    assert "调用失败" in ex.answer
    assert len(qa.history) == 1


def test_qa_requires_key(monkeypatch) -> None:
    import repo_agent.agents.qa as qa_mod

    monkeypatch.setattr(qa_mod, "_resolve_llm", lambda: None)
    raised = False
    try:
        RepoQA(_make_report())
    except RuntimeError:
        raised = True
    assert raised, "无 LLM key 时应抛 RuntimeError"
