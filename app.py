"""Streamlit 前端：GitHub 仓库深度体检 Agent 可交互 demo.

运行: streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from repo_agent.agents import Pipeline
from repo_agent.models import HealthReport, MetadataFinding


def score_badge(score: float | None) -> str:
    """分数徽章（None 表示未评估）."""
    if score is None:
        return "⚪ 未评估"
    if score >= 75:
        return f"🟢 {score:.0f}"
    if score >= 50:
        return f"🟡 {score:.0f}"
    return f"🔴 {score:.0f}"


def render_report(report: HealthReport) -> None:
    """把 HealthReport 渲染为 Streamlit 组件（抽离便于测试）."""
    st.header(f"🏥 {report.repo.full_name}")
    st.caption(f"生成时间: {report.generated_at.strftime('%Y-%m-%d %H:%M')}")

    st.metric("健康总分", f"{report.overall_score:.1f} / 100")

    meta = report.metadata
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stars", f"{meta.stars:,}")
    c2.metric("Forks", f"{meta.forks:,}")
    c3.metric("主语言", meta.language or "—")
    c4.metric("License", meta.license_name or "—")

    for f in report.findings:
        _render_finding(f)


def _render_finding(f: MetadataFinding) -> None:
    title = f"{score_badge(f.score)}  {f.category}"
    with st.expander(title, expanded=True):
        st.markdown(f"**结论**: {f.summary}")
        if f.evidences:
            st.markdown("**证据**")
            for e in f.evidences:
                st.markdown(f"- {e}")
        if f.suggestions:
            st.markdown("**改进建议**")
            for s in f.suggestions:
                st.markdown(f"- ⚠️ {s}")


def main() -> None:
    st.set_page_config(page_title="GitHub 仓库健康体检 Agent", layout="wide")
    st.title("🏥 GitHub 仓库深度体检 Agent")
    st.caption(
        "克隆代码 → 静态分析(ruff/radon/bandit) → 依赖漏洞(OSV) → 密钥检测 → 分级健康报告"
    )

    with st.sidebar:
        st.header("配置")
        token = st.text_input("GitHub Token（可选，提升限流）", type="password")
        skip_deep = st.checkbox("跳过深度分析（仅元数据，更快）", value=False)
        st.divider()
        st.markdown("输入格式：`owner/repo` 或完整 URL")

    repo_input = st.text_input("仓库地址", placeholder="fastapi/fastapi", key="repo")

    if st.button("开始体检", type="primary", use_container_width=True):
        if not repo_input.strip():
            st.warning("请输入仓库地址")
            return
        with st.spinner("正在克隆 + 静态分析 + 安全扫描…（首次可能需 30s–2min）"):
            try:
                pipe = Pipeline(token=token or None, skip_deep=skip_deep)
                report = pipe.run(repo_input.strip())
            except Exception as e:  # noqa: BLE001 - 前端层兜底
                st.error(f"体检失败: {e}")
                return
        render_report(report)
        st.success("体检完成 ✅")


if __name__ == "__main__":
    main()
