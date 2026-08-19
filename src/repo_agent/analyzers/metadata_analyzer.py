"""元数据维度分析器：基于 GitHub API 数据打分并给出证据与建议.

D1 阶段实现两个子维度：
- 活跃度（活跃维护）
- 社区/影响力（规模与信号）
后续接入 LangGraph 时，该模块作为 Agent 2 的调用单元。
"""

from __future__ import annotations

from repo_agent.models import (
    CommitInfo,
    ContributorInfo,
    IssueMetrics,
    MetadataFinding,
    RepoMetadata,
)


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def score_activity(meta: RepoMetadata, commits: list[CommitInfo], issues: IssueMetrics) -> MetadataFinding:
    """活跃度评分：关注最近推送、Issue/PR 处理时效、提交频率."""
    evidences: list[str] = []
    suggestions: list[str] = []
    score = 70.0  # 基础分

    days_since_push = meta.days_since_last_push
    evidences.append(f"最近代码推送: {days_since_push:.0f} 天前 ({meta.pushed_at.strftime('%Y-%m-%d')})")
    if meta.archived:
        score = 10.0
        evidences.append("仓库已归档 (archived)")
        suggestions.append("仓库已归档，代码不再维护，选型需谨慎")
        return MetadataFinding("活跃度", round(score, 1), "仓库已归档，处于停更状态", evidences, suggestions)

    if days_since_push <= 7:
        score += 15
    elif days_since_push <= 30:
        score += 5
    elif days_since_push <= 90:
        score -= 20
        suggestions.append(f"已 {days_since_push:.0f} 天未推送代码，维护频率偏低")
    else:
        score -= 40
        suggestions.append(f"已 {days_since_push:.0f} 天未推送代码，疑似停止维护")

    if commits:
        last = commits[0]
        evidences.append(f"最近提交: [{last.sha}] {last.message} ({last.author}, {last.date.strftime('%Y-%m-%d')})")
    if commits and len(commits) >= 2:
        # 提交跨度（天）：跨度越小说明越活跃；只用于展示，不做频率推断
        newest, oldest = commits[0], commits[-1]
        span_days = (newest.date - oldest.date).total_seconds() / 86400
        if span_days < 1e-6:
            evidences.append(f"最近 {len(commits)} 次提交集中在同一时段")
        else:
            evidences.append(f"最近 {len(commits)} 次提交时间跨度: {span_days:.2f} 天")

    if issues.merged_prs_30d == 0 and issues.open_prs == 0:
        suggestions.append("近 30 天无 PR 活动，社区贡献冷清")
    if issues.open_prs == 0:
        evidences.append("待处理 PR 数为 0，注意：无 Token 时部分接口受限，该值可能不准")
    evidences.append(f"近 30 天: 关闭 Issue {issues.closed_issues_30d} 个 / 合并 PR {issues.merged_prs_30d} 个 / 待处理 PR {issues.open_prs} 个")

    if issues.closed_issues_30d > 0:
        score += min(10, issues.closed_issues_30d * 2)

    return MetadataFinding("活跃度", round(clamp(score), 1),
                           f"仓库{'活跃' if score >= 75 else '维护中' if score >= 50 else '活跃度偏低'}",
                           evidences, suggestions)


def score_community(meta: RepoMetadata, contributors: list[ContributorInfo]) -> MetadataFinding:
    """社区/影响力评分：stars、forks、贡献者数量."""
    evidences: list[str] = []
    suggestions: list[str] = []
    score = 30.0

    evidences.append(f"Stars: {meta.stars:,} / Forks: {meta.forks:,}")
    if meta.stars >= 10000:
        score += 50
    elif meta.stars >= 3000:
        score += 40
    elif meta.stars >= 1000:
        score += 30
    elif meta.stars >= 100:
        score += 15
    else:
        suggestions.append("Stars 较少，社区认可度待验证")

    if meta.forks >= 1000:
        score += 10
    elif meta.forks >= 100:
        score += 5

    if contributors:
        names = ", ".join(f"{c.login}({c.contributions})" for c in contributors[:5])
        evidences.append(f"Top 贡献者: {names}")
        if len(contributors) >= 3:
            score += 10
        else:
            suggestions.append("核心贡献者较少，注意维护风险（bus factor）")

    if meta.topics:
        evidences.append(f"Topics: {', '.join(meta.topics[:8])}")

    return MetadataFinding("社区/影响力", round(clamp(score), 1),
                           f"{'高影响力' if score >= 75 else '有一定影响力' if score >= 50 else '影响力有限'}",
                           evidences, suggestions)


def analyze_metadata(
    meta: RepoMetadata,
    commits: list[CommitInfo],
    issues: IssueMetrics,
    contributors: list[ContributorInfo],
) -> list[MetadataFinding]:
    """元数据维度全部分析入口."""
    return [
        score_activity(meta, commits, issues),
        score_community(meta, contributors),
    ]
