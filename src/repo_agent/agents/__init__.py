"""Agent 包：各检查维度 Agent（后续 LangGraph 编排接入）."""

from __future__ import annotations

from pathlib import Path

from repo_agent.analyzers.code_analyzer import analyze_code_quality, detect_language
from repo_agent.analyzers.docs_analyzer import analyze_docs
from repo_agent.analyzers.metadata_analyzer import analyze_metadata
from repo_agent.analyzers.security_analyzer import analyze_security
from repo_agent.clients.cloner import FetchError, fetch_repo
from repo_agent.clients.github_client import GitHubClient
from repo_agent.models import HealthReport, MetadataFinding, RepoRef


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


class CodeAgent:
    """Agent 3：真实静态分析（D3，差异化核心）.

    接收已克隆的本地路径 + 检测到的主语言，做 ruff/radon/bandit 分析。
    安全约束：只读文件，绝不执行仓库内脚本。
    """

    def run(self, repo_path: Path, language: str | None) -> list:
        from repo_agent.models import MetadataFinding

        if language != "python":
            return [
                MetadataFinding(
                    "代码质量", None,
                    f"仓库主语言为 {language}，当前静态分析工具链仅支持 Python，该维度未评估",
                    [f"检测到 {language} 项目标记文件"], [],
                )
            ]
        finding = analyze_code_quality(repo_path, language)
        return [finding] if finding else []


class SecurityAgent:
    """Agent 4：安全分析（D4）.

    依赖漏洞(OSV, 仅 PyPI) + 硬编码密钥扫描(通用)。
    与 CodeAgent 共享同一份克隆，不重复拉取。
    """

    def run(self, repo_path: Path, language: str | None) -> list:
        return [analyze_security(repo_path, language)]


class Pipeline:
    """D1-D4 流水线：元数据 + 文档 + 代码质量 + 安全 + 报告输出.

    后续演进：LangGraph 状态图替换此处的顺序编排。
    """

    def __init__(self, token: str | None = None, skip_deep: bool = False) -> None:
        self.client = GitHubClient(token=token)
        self.skip_deep = skip_deep

    def run(self, input_text: str, out_path: str | None = None) -> str:
        """执行完整体检，返回报告文件路径."""
        ref = GitHubClient.parse_repo_ref(input_text)
        meta, findings = MetadataAgent(self.client).run(ref)
        findings = list(findings)
        findings.append(DocsAgent(self.client).run(ref, meta))

        if not self.skip_deep:
            clone_url = f"https://github.com/{ref.full_name}.git"
            try:
                fetched = fetch_repo(self.client, clone_url, ref.owner, ref.name)
            except FetchError as e:
                findings.append(
                    MetadataFinding(
                        "代码质量", 0.0, f"深度分析跳过: {e}",
                        [f"拉取源码失败（网络受限或仓库过大）: {e}"], [],
                    )
                )
            else:
                try:
                    language = detect_language(fetched.path)
                    findings.extend(CodeAgent().run(fetched.path, language))
                    findings.extend(SecurityAgent().run(fetched.path, language))
                finally:
                    fetched.cleanup()

        report = HealthReport(repo=ref, metadata=meta, findings=findings)

        from repo_agent.reporters.markdown_reporter import save_report

        if out_path is None:
            import os

            os.makedirs("reports", exist_ok=True)
            out_path = f"reports/{ref.full_name.replace('/', '_')}.md"
        return save_report(report, out_path)
