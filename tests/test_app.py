"""D5 测试：前端渲染辅助逻辑（不启动 streamlit server）.

render_report 内部调用 streamlit 组件，需 runtime 上下文，故仅测纯逻辑部分；
UI 交互通过 `streamlit run app.py` 手动验证。
"""
from datetime import datetime

from repo_agent.models import (
    HealthReport,
    MetadataFinding,
    RepoMetadata,
    RepoRef,
)

from app import score_badge


def _sample_report() -> HealthReport:
    ref = RepoRef("octocat", "hello")
    meta = RepoMetadata(
        full_name="octocat/hello",
        description="demo",
        language="Python",
        stars=100,
        forks=20,
        open_issues=5,
        created_at=datetime(2020, 1, 1),
        updated_at=datetime(2024, 1, 1),
        pushed_at=datetime(2024, 1, 1),
        license_name="MIT",
    )
    findings = [
        MetadataFinding("活跃度", 90.0, "活跃", ["最近提交 x"], ["建议 a"]),
        MetadataFinding("代码质量", None, "未评估", ["非 Python"], []),
    ]
    return HealthReport(repo=ref, metadata=meta, findings=findings)


def test_score_badge_levels():
    assert "🟢" in score_badge(90)
    assert "🟡" in score_badge(60)
    assert "🔴" in score_badge(30)
    assert "未评估" in score_badge(None)


def test_overall_skips_none():
    r = _sample_report()
    # 仅活跃度 90 计入，代码质量 None 应跳过
    assert r.overall_score == 90.0
