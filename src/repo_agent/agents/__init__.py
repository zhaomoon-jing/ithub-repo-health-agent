"""Agent 包：各检查维度 Agent（后续 LangGraph 编排接入）."""

from __future__ import annotations

from repo_agent.analyzers.docs_analyzer import analyze_docs
from repo_agent.analyzers.metadata_analyzer import analyze_metadata
from repo_agent.clients.github_client import GitHubClient
from repo_agent.models import HealthReport, RepoRef


class MetadataAgent:
    """Agent 1：元数据健康检查（D1 阶段即插即用）.

    后续在 LangGraph 中作为节点运行，当前提供同步调用接口。
    """

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def run(self, ref: RepoRef) -> list:
        """拉取元数据并分析，返回 findings 列表."""
        meta = self.client.get_repo(ref)
        commits = self.client.get_recent_commits(ref)
        issues = self.client.get_issue_metrics(ref)
        contributors = self.client.get_top_contributors(ref)
        return meta, analyze_metadata(meta, commits, issues, contributors)


class DocsAgent:
    """Agent 2：文档完整性检查（D2）."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def run(self, ref: RepoRef, meta) -> list:
        """拉取文档信息并分析，返回 finding（单条）."""
        readme = self.client.get_readme_raw(ref)
        root_files = self.client.get_root_files(ref)
        has_ci = self.client.has_ci_workflows(ref)
        return analyze_docs(meta, readme, root_files, has_ci)


class Pipeline:
    """D1-D2 最小流水线：元数据 Agent + 文档 Agent + 报告输出.

    后续演进：LangGraph 状态图替换此处的顺序编排。
    """

    def __init__(self, token: str | None = None) -> None:
        self.client = GitHubClient(token=token)

    def run(self, input_text: str, out_path: str | None = None) -> str:
        """执行完整体检，返回报告文件路径."""
        ref = GitHubClient.parse_repo_ref(input_text)
        meta, findings = MetadataAgent(self.client).run(ref)
        findings = list(findings)
        findings.append(DocsAgent(self.client).run(ref, meta))
        report = HealthReport(repo=ref, metadata=meta, findings=findings)

        from repo_agent.reporters.markdown_reporter import save_report

        if out_path is None:
            import os

            os.makedirs("reports", exist_ok=True)
            out_path = f"reports/{ref.full_name.replace('/', '_')}.md"
        return save_report(report, out_path)
