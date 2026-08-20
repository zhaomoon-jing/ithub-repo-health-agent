"""Markdown 报告生成器."""

from __future__ import annotations

from repo_agent.models import HealthReport


def _priority_badge(score: float | None) -> str:
    if score is None:
        return "⚪"
    if score >= 75:
        return "🟢"
    if score >= 50:
        return "🟡"
    return "🔴"


def render_markdown(report: HealthReport) -> str:
    """渲染 Markdown 报告."""
    meta = report.metadata
    lines: list[str] = []
    lines.append(f"# 🏥 GitHub 仓库健康报告: {report.repo.full_name}")
    lines.append("")
    lines.append(f"> 生成时间: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 总体评分
    lines.append("## 总览")
    lines.append("")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| **健康总分** | **{report.overall_score}/100** |")
    lines.append(f"| Stars | {meta.stars:,} |")
    lines.append(f"| Forks | {meta.forks:,} |")
    lines.append(f"| 主语言 | {meta.language or '未知'} |")
    lines.append(f"| 描述 | {meta.description or '无'} |")
    lines.append(f"| License | {meta.license_name or '无 License'} |")
    lines.append(f"| 最近推送 | {meta.pushed_at.strftime('%Y-%m-%d')} ({meta.days_since_last_push:.0f} 天前) |")
    lines.append(f"| 创建时间 | {meta.created_at.strftime('%Y-%m-%d')} |")
    lines.append(f"| Topics | {', '.join(meta.topics[:8]) if meta.topics else '无'} |")
    lines.append("")

    # 分维度
    lines.append("## 分维度诊断")
    lines.append("")
    for finding in report.findings:
        if finding.score is None:
            lines.append(f"### ⚪ {finding.category}: 未评估")
        else:
            lines.append(f"### {_priority_badge(finding.score)} {finding.category}: {finding.score}/100")
        lines.append("")
        lines.append(f"**结论**: {finding.summary}")
        lines.append("")
        if finding.evidences:
            lines.append("**证据**:")
            lines.append("")
            for ev in finding.evidences:
                lines.append(f"- {ev}")
            lines.append("")
        if finding.suggestions:
            lines.append("**改进建议**:")
            lines.append("")
            for s in finding.suggestions:
                lines.append(f"- ⚠️ {s}")
            lines.append("")

    # 尾部
    lines.append("---")
    lines.append("")
    lines.append("*由 repo-health-agent 生成 · 元数据来自 GitHub 公开 API，代码级深度体检通过克隆仓库后 ruff/radon/bandit 静态分析完成（带文件行号证据）*")
    lines.append("")
    return "\n".join(lines)


def save_report(report: HealthReport, out_path: str) -> str:
    """保存报告到文件，返回文件路径."""
    content = render_markdown(report)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path
