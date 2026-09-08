"""Streamlit 前端：GitHub 仓库深度体检 Agent
运行: streamlit run app_addmail_sidebar.py
SMTP 邮件配置读取 .env，支持465(SSL) / 587(STARTTLS)
邮件包含：完整体检报告 + 多轮对话历史（如有）
"""
from __future__ import annotations
import os
import smtplib
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import streamlit as st
from dotenv import load_dotenv
from repo_agent.agents import Pipeline
from repo_agent.agents.qa import RepoQA
from repo_agent.models import HealthReport, MetadataFinding

load_dotenv()


def send_report_email(report: HealthReport, to_email: str, chat_messages: list) -> None:
    """发送完整仓库健康报告邮件，附带多轮对话历史"""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_str = os.getenv("SMTP_PORT")
    smtp_sender = os.getenv("SMTP_SENDER")
    smtp_auth_code = os.getenv("SMTP_AUTH_CODE")

    if not all([smtp_host, smtp_port_str, smtp_sender, smtp_auth_code]):
        raise RuntimeError("SMTP配置不全，请检查.env：SMTP_HOST / SMTP_PORT / SMTP_SENDER / SMTP_AUTH_CODE")
    try:
        smtp_port = int(smtp_port_str)
    except (ValueError, TypeError):
        raise RuntimeError(f"SMTP_PORT 必须是数字，当前读取到：{smtp_port_str!r}")

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_sender
    msg["To"] = to_email.strip()
    msg["Subject"] = f"GitHub 仓库体检报告｜{report.repo.full_name}"

    # ---------------------- 纯文本降级内容 ----------------------
    plain_body_lines = [
        f"仓库：{report.repo.full_name}",
        f"生成时间：{report.generated_at.strftime('%Y-%m-%d %H:%M')}",
        f"健康总分：{report.overall_score:.1f} / 100",
    ]
    if report.summary:
        plain_body_lines.append(f"\n总体诊断：\n{report.summary}")

    for f in report.findings:
        score_text = f"{f.score:.1f}" if f.score is not None else "未评估"
        plain_body_lines.append(f"\n【{f.category}】 分数:{score_text}")
        plain_body_lines.append(f"结论：{f.summary}")
        if f.evidences:
            plain_body_lines.append("证据：")
            for e in f.evidences:
                plain_body_lines.append(f" - {e}")
        if f.suggestions:
            plain_body_lines.append("改进建议：")
            for s in f.suggestions:
                plain_body_lines.append(f" - ⚠️ {s}")

    # 追加多轮对话（纯文本）
    if chat_messages:
        plain_body_lines.append("\n" + "="*60)
        plain_body_lines.append("💬 多轮对话历史")
        for m in chat_messages:
            if m["role"] == "user":
                plain_body_lines.append(f"\n【用户】：{m['content']}")
            else:
                intent = m.get("intent", "")
                fu = m.get("followup", "")
                plain_body_lines.append(f"\n【AI】(意图:{intent})：{m['content']}")
                if fu:
                    plain_body_lines.append(f"可继续追问提示：{fu}")

    plain_text = "\n".join(plain_body_lines)

    # ---------------------- HTML富文本完整报告 ----------------------
    meta = report.metadata
    html_parts = []
    html_parts.append("""
    <html>
    <head>
    <meta charset="utf-8">
    <style>
    body{font-family:Arial,sans-serif;font-size:14px;}
    .box{border:1px solid #ccc;padding:12px;margin:8px 0;border-radius:6px;}
    .chat-box{border:1px solid #b8c8e0;padding:10px;margin:6px 0;border-radius:6px;}
    .user{background:#e8f4ff;padding:8px;border-radius:4px;margin:4px 0;}
    .assistant{background:#f7f7f7;padding:8px;border-radius:4px;margin:4px 0;}
    .green{color:#28a745;font-weight:bold;}
    .yellow{color:#d39e00;font-weight:bold;}
    .red{color:#dc3545;font-weight:bold;}
    .gray{color:#666;}
    </style>
    </head>
    <body>
    """)
    html_parts.append(f"<h2>🏥 {report.repo.full_name}</h2>")
    html_parts.append(f"<p>生成时间：{report.generated_at.strftime('%Y-%m-%d %H:%M')}</p>")
    html_parts.append(f"<h3>健康总分：{report.overall_score:.1f} / 100</h3>")

    html_parts.append("<div class='box'>")
    html_parts.append(f"<p>⭐ Stars: {meta.stars:,} &nbsp;&nbsp; 🍴 Forks: {meta.forks:,}</p>")
    html_parts.append(f"<p>主语言：{meta.language or '—'} &nbsp;&nbsp; License：{meta.license_name or '—'}</p>")
    html_parts.append("</div>")

    if report.summary:
        html_parts.append(f"<div class='box'><h4>🧠 总体诊断</h4><p>{report.summary}</p></div>")

    # 遍历检测项
    for f in report.findings:
        if f.score is None:
            score_html = "<span class='gray'>⚪ 未评估</span>"
        elif f.score >=75:
            score_html = f"<span class='green'>🟢 {f.score:.0f}</span>"
        elif f.score >=50:
            score_html = f"<span class='yellow'>🟡 {f.score:.0f}</span>"
        else:
            score_html = f"<span class='red'>🔴 {f.score:.0f}</span>"

        html_parts.append(f"<div class='box'>")
        html_parts.append(f"<h4>{score_html} &nbsp;{f.category}</h4>")
        html_parts.append(f"<p><strong>结论：</strong>{f.summary}</p>")

        if f.evidences:
            html_parts.append("<p><strong>证据：</strong></p><ul>")
            for e in f.evidences:
                html_parts.append(f"<li>{e}</li>")
            html_parts.append("</ul>")

        if f.suggestions:
            html_parts.append("<p><strong>改进建议：</strong></p><ul>")
            for s in f.suggestions:
                html_parts.append(f"<li>⚠️ {s}</li>")
            html_parts.append("</ul>")
        html_parts.append("</div>")

    # 追加多轮对话HTML区块（有消息才输出）
    if chat_messages:
        html_parts.append("<hr/>")
        html_parts.append("<h3>💬 多轮追问对话历史</h3>")
        for m in chat_messages:
            if m["role"] == "user":
                html_parts.append(f"<div class='chat-box user'><b>👤 用户</b><br/>{m['content']}</div>")
            else:
                intent = m.get("intent","")
                fu = m.get("followup","")
                html_parts.append(f"<div class='chat-box assistant'><b>🤖 AI (意图:{intent})</b><br/>{m['content']}")
                if fu:
                    html_parts.append(f"<br/><i>💡可继续追问：{fu}</i>")
                html_parts.append("</div>")

    html_parts.append("</body></html>")
    html_body = "".join(html_parts)

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_sender, smtp_auth_code)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_sender, smtp_auth_code)
            server.send_message(msg)


def score_badge(score: float | None) -> str:
    """分数状态徽章"""
    if score is None:
        return "⚪ 未评估"
    if score >= 75:
        return f"🟢 {score:.0f}"
    if score >= 50:
        return f"🟡 {score:.0f}"
    return f"🔴 {score:.0f}"


def render_report(report: HealthReport) -> None:
    st.header(f"🏥 {report.repo.full_name}")
    st.caption(f"生成时间: {report.generated_at.strftime('%Y-%m-%d %H:%M')}")
    st.metric("健康总分", f"{report.overall_score:.1f} / 100")
    st.info("如需发送报告，请在左侧边栏填写邮箱后点击【发送当前报告】")

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
    """基于报告的多轮追问对话"""
    st.divider()
    st.subheader("💬 多轮追问")
    st.caption("基于上方报告继续提问，支持上下文追踪。示例：“代码维度怎么改进？”“和文档比哪个更弱？”")

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
            st.session_state.qa_messages.append({
                "role": "assistant",
                "content": ex.answer,
                "intent": ex.intent.value,
                "followup": ex.followup,
            })


def main() -> None:
    st.set_page_config(page_title="GitHub 仓库健康体检 Agent", layout="wide")
    st.title("🏥 GitHub 仓库深度体检 Agent")
    st.caption("克隆代码 → 静态分析(ruff/radon/bandit) → 依赖漏洞(OSV) → 密钥检测 → 分级健康报告")

    # 会话状态初始化
    if "current_report" not in st.session_state:
        st.session_state.current_report = None
    if "qa_messages" not in st.session_state:
        st.session_state.qa_messages = []
    if "stored_email" not in st.session_state:
        st.session_state.stored_email = ""

    with st.sidebar:
        st.header("配置")
        token_raw = st.text_input("GitHub Token（可选，提升限流）", type="password")
        # 清洗token：过滤非ASCII字符，避免httpx请求头编码报错
        if token_raw:
            token = "".join(c for c in token_raw.strip() if ord(c) < 128)
            token = token if token else None
        else:
            token = None

        skip_deep = st.checkbox("跳过深度分析（仅元数据，更快）", value=False)
        use_llm = st.checkbox(
            "启用 LLM 总体诊断（需 ZHIPU_API_KEY 或 DEEPSEEK_API_KEY）", value=False
        )
        st.divider()

        st.subheader("📧 报告邮件")
        # 直接用key绑定stored_email，无需on_change中间回调
        st.text_input(
            "接收报告邮箱",
            placeholder="your@example.com",
            key="stored_email",
        )
        stored_email = st.session_state["stored_email"].strip()
        send_btn_disabled = (st.session_state.current_report is None) or (not stored_email or "@" not in stored_email)

        if st.button(
            "发送当前报告",
            key="send_report_btn",
            disabled=send_btn_disabled
        ):
            try:
                with st.spinner("正在发送邮件…"):
                    send_report_email(
                        st.session_state.current_report,
                        stored_email,
                        st.session_state.qa_messages
                    )
                st.success(f"✅ 报告已发送至 {stored_email}")
            except Exception as e:  # noqa: BLE001
                st.error(f"发送失败：{e}")

        st.divider()
        st.markdown("输入格式：`owner/repo` 或完整 URL")

    repo_input = st.text_input("仓库地址", placeholder="fastapi/fastapi", key="repo")

    if st.button("开始体检", type="primary", use_container_width=True):
        if not repo_input.strip():
            st.warning("请输入仓库地址")
        else:
            with st.spinner("正在克隆 + 静态分析 + 安全扫描…（首次可能需 30s–2min）"):
                try:
                    pipe = Pipeline(token=token or None, skip_deep=skip_deep, use_llm=use_llm)
                    report = pipe.run(repo_input.strip())
                    st.session_state.current_report = report
                    # 关键：体检完成后强制rerun，让sidebar的发送按钮用最新current_report重新计算disabled状态
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    err_msg = str(e)
                    if "401 Unauthorized" in err_msg:
                        st.error("GitHub API鉴权失败(401)：Token无效/已过期，或清空Token尝试匿名模式")
                    else:
                        st.error(f"体检失败: {e}")
                    traceback.print_exc()
                    with st.expander("🔍 查看完整错误堆栈（排查用）", expanded=True):
                        st.exception(e)

    # 全局渲染：只要session存在报告就渲染，重渲染不会丢失UI
    report = st.session_state.get("current_report")
    if report is not None:
        render_report(report)
        st.success("体检完成 ✅")
        try:
            if ("qa" not in st.session_state or st.session_state.get("qa_repo") != report.repo.full_name):
                st.session_state.qa = RepoQA(report)
                st.session_state.qa_repo = report.repo.full_name
                st.session_state.qa_messages = []
            render_chat(report, st.session_state.qa)
        except Exception as e:  # noqa: BLE001
            st.info(f"💬 多轮追问需配置 LLM key（ZHIPU_API_KEY / DEEPSEEK_API_KEY）：{e}")


if __name__ == "__main__":
    main()