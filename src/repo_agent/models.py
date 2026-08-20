"""数据模型定义."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RepoRef:
    """仓库引用：owner/repo 或完整 URL."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class RepoMetadata:
    """GitHub API 返回的仓库元数据."""

    full_name: str
    description: Optional[str]
    language: Optional[str]
    stars: int
    forks: int
    open_issues: int
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime
    license_name: Optional[str]
    topics: list[str] = field(default_factory=list)
    has_issues: bool = False
    has_wiki: bool = False
    archived: bool = False
    homepage: Optional[str] = None

    @property
    def days_since_last_push(self) -> float:
        """距离最后一次代码推送的天数."""
        return (datetime.now(self.pushed_at.tzinfo) - self.pushed_at).total_seconds() / 86400

    @property
    def days_since_created(self) -> float:
        """仓库创建至今的天数."""
        return (datetime.now(self.created_at.tzinfo) - self.created_at).total_seconds() / 86400


@dataclass
class CommitInfo:
    """最近提交信息."""

    sha: str
    message: str
    author: str
    date: datetime
    url: str


@dataclass
class ContributorInfo:
    """贡献者信息."""

    login: str
    contributions: int


@dataclass
class IssueMetrics:
    """Issue/PR 处理时效统计."""

    open_issues: int
    closed_issues_30d: int
    open_prs: int
    merged_prs_30d: int


@dataclass
class MetadataFinding:
    """元数据维度的诊断结果."""

    category: str  # 例如 "活跃度" / "社区"
    score: float | None  # 0-100，None 表示该维度未评估（如非 Python 仓库）
    summary: str
    evidences: list[str] = field(default_factory=list)  # 支持结论的原始数据
    suggestions: list[str] = field(default_factory=list)  # 改进建议


@dataclass
class HealthReport:
    """体检报告（D1 阶段：仅元数据维度）."""

    repo: RepoRef
    metadata: RepoMetadata
    findings: list[MetadataFinding] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def overall_score(self) -> float:
        """加权总评分（跳过未评估维度）."""
        scored = [f.score for f in self.findings if f.score is not None]
        if not scored:
            return 0.0
        return round(sum(scored) / len(scored), 1)
