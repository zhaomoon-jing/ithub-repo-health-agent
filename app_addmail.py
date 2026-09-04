"""Streamlit 前端：GitHub 仓库深度体检 Agent 可交互 demo.
运行: streamlit run app.py
"""
from __future__ import annotations
import streamlit as st
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

from repo_agent.agents import Pipeline
from repo_agent.agents.qa import RepoQA
from repo_agent.models import HealthReport, MetadataFinding

# 加载环境变量 SMTP 配置
load_dotenv()

def send_report_email(report: HealthReport, to_email: str) -> None:
    """发送仓库健康报告到指定邮箱"""
    smtp_cfg = {
        "host": os.getenv("SMTP_HOST"),
        "port": int(os.getenv("SMTP_PORT")),
        "sender": os.getenv("SMTP_SENDER"),
        "auth_code": os.getenv("SMTP_AUTH_CODE"),
    }
    msg = MIMEMultipart()
    msg["From"] = smtp_cfg["sender"]
    msg["To"] = to_email.strip()
    msg["Subject"] = f"GitHub仓库体检报告｜{report.repo.full_name}"

    body_lines = []
    body_lines.append(f"仓库：{report.repo.full_name}")
    body_lines.append(f"生成时间：{report.generated_at.strftime('%Y-%m-%d %H:%M')}")
    body_lines.append(f"健康总分：{report.overall_score:.1f} /100")
    if report.summary:
        body_lines.append(f"\n总体诊断：\n{report.summary}")
    body = "\n".join(body_lines)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as server:
        server.starttls()
        server.login(smtp_cfg["sender"], smtp_cfg["auth_code"])
        server.send_message(msg)


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
    if report.summary:
        st.subheader("🧠 总体诊断")
        st.info(report.summary)
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


def render_chat(report: HealthReport, qa: RepoQA) -> None:
    """D8: 基于报告上下文的多轮追问对话（含意图识别展示）."""
    st.divider()
    st.subheader("💬 多轮追问")
    st.caption(
        "基于上方报告继续提问，支持上下文追踪。示例："
        "“代码维度怎么改进？”“和文档比哪个更弱？”"
    )
    for m in st.session_state.qa_messages:
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.markdown(m["content"])
            else:
                st.markdown(f"**意图 · {m['intent']}**  \n{m['content']}")
                if m.get("followup"):
                    st.caption(f"💡 可继续追问：{m['followup']}")
    if prompt := st.chat_input("追问这个仓库…"):
        st.session_state.qa_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            ex = qa.ask(prompt)
            st.markdown(f"**意图 · {ex.intent.value}**  \n{ex.answer}")
            if ex.followup:
                st.caption(f"💡 可继续追问：{ex.followup}")
            st.session_state.qa_messages.append(
                {
                    "role": "assistant",
                    "content": ex.answer,
                    "intent": ex.intent.value,
                    "followup": ex.followup,
                }
            )


def main() -> None:
    st.set_page_config(page_title="GitHub 仓库健康体检 Agent", layout="wide")
    st.title("🏥 GitHub 仓库深度体检 Agent")
    st.caption(
        "克隆代码 → 静态分析(ruff/radon/bandit) → 依赖漏洞(OSV) → 密钥检测 → 分级健康报告"
    )

    # session_state 保存当前生成的报告，用于后续补发邮件
    if "current_report" not in st.session_state:
        st.session_state.current_report = None

    with st.sidebar:
        st.header("配置")
        token = st.text_input("GitHub Token（可选，提升限流）", type="password")
        skip_deep = st.checkbox("跳过深度分析（仅元数据，更快）", value=False)
        use_llm = st.checkbox("启用 LLM 总体诊断（需 ZHIPU_API_KEY 或 DEEPSEEK_API_KEY）", value=False)

        st.divider()
        # ========== 方案1：常驻侧边栏的邮箱输入框 ==========
        st.subheader("📧 邮件接收")
        recipient_email = st.text_input("接收报告邮箱", placeholder="xxx@example.com", key="sidebar_email")
        send_manual = st.button("发送当前报告", disabled=st.session_state.current_report is None)
        if send_manual:
            email_val = recipient_email.strip()
            if not email_val or "@" not in email_val:
                st.sidebar.warning("请输入合法邮箱地址")
            else:
                try:
                    with st.spinner("正在发送邮件..."):
                        send_report_email(st.session_state.current_report, email_val)
                    st.sidebar.success(f"✅ 已发送至 {email_val}")
                except Exception as err:
                    st.sidebar.error(f"发送失败：{err}")

        st.divider()
        st.markdown("输入格式：`owner/repo` 或完整 URL")

    repo_input = st.text_input("仓库地址", placeholder="fastapi/fastapi", key="repo")
    if st.button("开始体检", type="primary", use_container_width=True):
        if not repo_input.strip():
            st.warning("请输入仓库地址")
            return
        with st.spinner("正在克隆 + 静态分析 + 安全扫描…（首次可能需 30s–2min）"):
            try:
                pipe = Pipeline(token=token or None, skip_deep=skip_deep, use_llm=use_llm)
                report = pipe.run(repo_input.strip())
                # 将报告存入session_state，供侧边栏补发邮件使用
                st.session_state.current_report = report
            except Exception as e:  # noqa: BLE001 - 前端层兜底
                st.error(f"体检失败: {e}")
                return

        render_report(report)
        st.success("体检完成 ✅")

        # 如果侧边栏已经填好了邮箱，体检跑完自动发送
        email_input = st.session_state.get("sidebar_email", "").strip()
        if email_input and "@" in email_input:
            try:
                with st.spinner("自动发送报告邮件..."):
                    send_report_email(report, email_input)
                st.success(f"✅ 报告已自动发送到 {email_input}")
            except Exception as err:
                st.error(f"自动邮件发送失败：{err}")

        # ---- D8: 基于报告的多轮追问对话 ----
        if "qa_messages" not in st.session_state:
            st.session_state.qa_messages = []
        try:
            if ("qa" not in st.session_state
                    or st.session_state.get("qa_repo") != report.repo.full_name):
                st.session_state.qa = RepoQA(report)
                st.session_state.qa_repo = report.repo.full_name
                st.session_state.qa_messages = []
            render_chat(report, st.session_state.qa)
        except Exception as e:  # noqa: BLE001 - 无 LLM key 时优雅降级
            st.info(
                f"💬 多轮追问需配置 LLM key（ZHIPU_API_KEY / DEEPSEEK_API_KEY）：{e}"
            )


if __name__ == "__main__":
    main()
