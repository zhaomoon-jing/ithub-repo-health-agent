"""Agent 包：各检查维度 Agent（后续 LangGraph 编排接入）."""

from __future__ import annotations

from repo_agent.analyzers.code_analyzer import analyze_code_quality, detect_language
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


class CodeAgent:
    """Agent 3：拉取仓库源码 + 真实静态分析（D3，差异化核心）.

    安全约束：只读文件，绝不执行仓库内脚本；分析完即清理临时目录。
    获取方式：优先 codeload tarball（国内网络友好），失败降级 git clone。
    """

    def __init__(self, client: GitHubClient, clone_url_template: str = "https://github.com/{full_name}.git") -> None:
        self.client = client
        self.clone_url_template = clone_url_template

    def run(self, ref: RepoRef, meta) -> list:
        """执行深度分析，返回 findings 列表（非 Python 仓库返回说明）."""
        from repo_agent.clients.cloner import FetchError, fetch_repo
        from repo_agent.models import MetadataFinding

        clone_url = self.clone_url_template.format(full_name=ref.full_name)
        try:
            fetched = fetch_repo(self.client, clone_url, ref.owner, ref.name)
        except FetchError as e:
            return [
                MetadataFinding(
                    "代码质量", 0.0, f"深度分析跳过: {e}",
                    [f"拉取源码失败（网络受限或仓库过大）: {e}"], [],
                )
            ]

        try:
            language = detect_language(fetched.path)
            if language != "python":
                return [
                    MetadataFinding(
                        "代码质量", None,
                        f"仓库主语言为 {language}，当前静态分析工具链仅支持 Python，该维度未评估",
                        [f"检测到 {language} 项目标记文件"], [],
                    )
                ]
            finding = analyze_code_quality(fetched.path, language)
            return [finding] if finding else []
        finally:
            fetched.cleanup()


class Pipeline:
    """D1-D3 最小流水线：元数据 + 文档 + 代码质量 + 报告输出.

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
            findings.extend(CodeAgent(self.client).run(ref, meta))
        report = HealthReport(repo=ref, metadata=meta, findings=findings)

        from repo_agent.reporters.markdown_reporter import save_report

        if out_path is None:
            import os

            os.makedirs("reports", exist_ok=True)
            out_path = f"reports/{ref.full_name.replace('/', '_')}.md"
        return save_report(report, out_path)
