"""Agent 包：LangGraph 状态图编排的各检查维度 Agent.

D6/D7 合并分支：把原顺序 Pipeline 重构成 langgraph.StateGraph，显式建模
- 状态累积：HealthReport 的各维度 finding 在节点间传递并累积
- 并行分支：元数据 / 文档 互不依赖，代码 / 安全 共享克隆后并行
- 条件路由：语言检测决定是否进入深度分析（非 Python 仓库跳过）
- 容错降级：克隆失败不崩溃，自动降级为浅层报告
- LLM 总评：SummaryAgent 在图的末端基于各维度分数生成总体诊断（可降级为规则）
"""

from __future__ import annotations

import operator
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

from repo_agent.analyzers.code_analyzer import analyze_code_quality, detect_language
from repo_agent.analyzers.docs_analyzer import analyze_docs
from repo_agent.analyzers.metadata_analyzer import analyze_metadata
from repo_agent.analyzers.security_analyzer import analyze_security
from repo_agent.clients.cloner import FetchError, fetch_repo
from repo_agent.clients.github_client import GitHubClient
from repo_agent.models import HealthReport, MetadataFinding, RepoMetadata, RepoRef

# ---------------------------------------------------------------------------
# 状态定义
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """LangGraph 在节点间传递的状态。

    findings 用 operator.add 做 reducer，保证多个并行节点各自产出的诊断
    结论被"累积"而非互相覆盖——这是状态图相比裸函数顺序调用的核心差异之一。
    """

    repo_input: str
    ref: RepoRef | None
    metadata: RepoMetadata | None
    findings: Annotated[list, operator.add]
    clone_path: str | None  # 克隆的项目根（分析用）；None 表示未克隆/克隆失败
    clone_root: str | None  # 克隆的外层临时目录（finalize 清理用）
    language: str | None
    fetch_error: str | None
    summary: str | None
    report: HealthReport | None  # 由 finalize 节点填充，供 run 返回
    use_llm: bool  # 是否启用 LLM 总评（默认 False，仅最后验证开启）


# ---------------------------------------------------------------------------
# 各维度 Agent（节点函数复用，与 D1-D4 逻辑一致）
# ---------------------------------------------------------------------------


class MetadataAgent:
    """Agent 1：元数据健康检查."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def run(self, ref: RepoRef) -> list:
        meta = self.client.get_repo(ref)
        commits = self.client.get_recent_commits(ref)
        issues = self.client.get_issue_metrics(ref)
        contributors = self.client.get_top_contributors(ref)
        return meta, analyze_metadata(meta, commits, issues, contributors)


class DocsAgent:
    """Agent 2：文档完整性检查."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def run(self, ref: RepoRef, meta) -> list:
        readme = self.client.get_readme_raw(ref)
        root_files = self.client.get_root_files(ref)
        has_ci = self.client.has_ci_workflows(ref)
        return [analyze_docs(meta, readme, root_files, has_ci)]


class CodeAgent:
    """Agent 3：真实静态分析（差异化核心，只读不执行）."""

    def run(self, repo_path: Path, language: str | None) -> list:
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
    """Agent 4：安全分析（依赖漏洞 + 硬编码密钥）."""

    def run(self, repo_path: Path, language: str | None) -> list:
        return [analyze_security(repo_path, language)]


# ---------------------------------------------------------------------------
# LLM 总评（D6）：默认规则降级，环境存在 ZHIPU_API_KEY 时调用智谱
# ---------------------------------------------------------------------------


def _build_summary_prompt(report: HealthReport) -> str:
    """构造给 LLM 的总体诊断提示词（中文）."""
    lines = [f"你是一名资深软件工程架构师，正在审阅 GitHub 仓库 {report.repo.full_name} 的健康度体检报告。"]
    lines.append(f"综合健康分：{report.overall_score}/100。")
    lines.append("各维度评分与结论如下：")
    for f in report.findings:
        score = "未评估" if f.score is None else f"{f.score}/100"
        lines.append(f"- [{f.category}] {score}：{f.summary}")
        for s in f.suggestions[:2]:
            lines.append(f"    · 建议：{s}")
    lines.append(
        "请用 3-5 句中文给出总体诊断：先判断仓库整体健康水平，再点出最值得优先处理"
        "的 1-2 个风险，最后给一句可操作的改进方向。不要重复列出每个维度分数。"
    )
    return "\n".join(lines)


def rule_based_summary(report: HealthReport) -> str:
    """无 LLM 时的规则总评（降级路径）."""
    scored = [(f.category, f.score) for f in report.findings if f.score is not None]
    if not scored:
        return "未获取到有效维度评分，无法生成总体诊断。"
    scored.sort(key=lambda x: x[1])
    weakest = scored[:2]
    overall = report.overall_score
    level = "优秀" if overall >= 85 else "良好" if overall >= 70 else "一般" if overall >= 50 else "堪忧"
    parts = [f"综合健康分 {overall}/100，整体水平{level}。"]
    weak_desc = "、".join(f"{c}({s}分)" for c, s in weakest)
    parts.append(f"最薄弱维度：{weak_desc}，建议优先处理。")
    return "".join(parts)


def _resolve_llm() -> "tuple[str, str, str] | None":
    """解析可用的 LLM 提供商（智谱优先，其次 DeepSeek，均为 OpenAI 兼容）.

    返回 (api_key, endpoint, model)；无可用 key 时返回 None（降级为规则总评）。
    环境变量名：ZHIPU_API_KEY / DEEPSEEK_API_KEY。
    """
    zhipu = os.environ.get("ZHIPU_API_KEY")
    if zhipu:
        return (zhipu, "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-flash")
    deepseek = os.environ.get("DEEPSEEK_API_KEY")
    if deepseek:
        return (deepseek, "https://api.deepseek.com/v1/chat/completions", "deepseek-chat")
    return None


def call_llm_summary(report: HealthReport, api_key: str, endpoint: str, model: str) -> str:
    """调用 LLM 生成总体诊断（OpenAI 兼容 endpoint，httpx 直连，不引入额外依赖）.

    仅在最后验证阶段调用一次；调用失败由上层捕获并降级为规则总评。
    """
    import httpx

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _build_summary_prompt(report)}],
        "temperature": 0.3,
    }
    resp = httpx.post(endpoint, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# 状态图编排
# ---------------------------------------------------------------------------


class Pipeline:
    """LangGraph 编排的仓库体检流水线（替代原顺序 Pipeline）.

    图的拓扑（体现"真编排"而非套壳）：
        START → parse
        parse → metadata ┐
        parse → docs     ├─ 并行（均不依赖克隆）
        parse → router   ┘（router 同时做条件路由：是否进入深度分析）
        router --skip--> summary
        router --deep--> clone → code ┐
                              clone → security ┘ 并行（共享克隆）
        code/security → summary → finalize → END
    """

    def __init__(self, token: str | None = None, skip_deep: bool = False, use_llm: bool = False) -> None:
        self.client = GitHubClient(token=token)
        self.skip_deep = skip_deep
        self.use_llm = use_llm
        self.graph = self._build_graph()

    # ---- 节点 ----
    def _parse(self, state: AgentState) -> dict:
        try:
            ref = GitHubClient.parse_repo_ref(state["repo_input"])
            return {"ref": ref}
        except ValueError as e:
            return {"ref": None, "summary": f"仓库地址解析失败：{e}"}

    def _metadata(self, state: AgentState) -> dict:
        ref = state["ref"]
        if not ref:
            return {}
        meta, findings = MetadataAgent(self.client).run(ref)
        return {"metadata": meta, "findings": findings}

    def _docs(self, state: AgentState) -> dict:
        ref = state["ref"]
        if not ref:
            return {}
        # LangGraph 中 metadata 与 docs 并行执行：docs 节点启动时读不到 metadata
        # 节点的写入（state 仍是 superstep 初始快照），故缺失时自行补一次 get_repo，
        # 既保留并行结构，又保证文档完整性维度不丢失。
        meta = state.get("metadata") or self.client.get_repo(ref)
        return {"findings": DocsAgent(self.client).run(ref, meta)}

    def _route_deep(self, state: AgentState) -> str:
        """条件边：是否进入深度克隆分析."""
        if self.skip_deep or not state.get("ref"):
            return "skip"
        return "deep"

    def _clone(self, state: AgentState) -> dict:
        ref = state["ref"]
        clone_url = f"https://github.com/{ref.full_name}.git"
        try:
            fetched = fetch_repo(self.client, clone_url, ref.owner, ref.name)
        except FetchError as e:
            return {"fetch_error": str(e), "clone_path": None}
        language = detect_language(fetched.path)
        return {"clone_path": str(fetched.path), "clone_root": str(fetched.root), "language": language}

    def _code(self, state: AgentState) -> dict:
        if not state.get("clone_path"):
            return {}
        return {"findings": CodeAgent().run(Path(state["clone_path"]), state["language"])}

    def _security(self, state: AgentState) -> dict:
        if not state.get("clone_path"):
            return {}
        return {"findings": SecurityAgent().run(Path(state["clone_path"]), state["language"])}

    def _summary(self, state: AgentState) -> dict:
        if not state.get("ref") or not state.get("metadata"):
            return {"summary": state.get("summary") or "无法生成总评。"}
        report = HealthReport(
            repo=state["ref"], metadata=state["metadata"], findings=state["findings"]
        )
        if self.use_llm:
            cfg = _resolve_llm()
            if cfg:
                try:
                    return {"summary": call_llm_summary(report, *cfg)}
                except Exception as e:  # noqa: BLE001 - 失败不阻塞流水线，但提示降级
                    print(f"[warn] LLM 总评调用失败，已降级为规则总评: {e}", file=sys.stderr)
        return {"summary": rule_based_summary(report)}

    def _finalize(self, state: AgentState) -> dict:
        ref = state["ref"]
        if not ref or not state.get("metadata"):
            return {"report": None}
        report = HealthReport(
            repo=ref,
            metadata=state["metadata"],
            findings=state["findings"],
            summary=state.get("summary"),
        )
        # 清理克隆的外层临时目录（若曾克隆），确保整棵子树被移除
        if state.get("clone_root"):
            shutil.rmtree(state["clone_root"], ignore_errors=True)
        return {"report": report}

    # ---- 构图 ----
    def _build_graph(self) -> "CompiledGraph":
        builder = StateGraph(AgentState)
        builder.add_node("parse", self._parse)
        builder.add_node("metadata", self._metadata)
        builder.add_node("docs", self._docs)
        builder.add_node("router", lambda s: {})  # 仅作 fan-in + 条件路由锚点
        builder.add_node("clone", self._clone)
        builder.add_node("code", self._code)
        builder.add_node("security", self._security)
        builder.add_node("summary", self._summary)
        builder.add_node("finalize", self._finalize)

        builder.set_entry_point("parse")
        # 并行分支：metadata / docs 在解析后独立运行
        builder.add_edge("parse", "metadata")
        builder.add_edge("parse", "docs")
        # fan-in 到 router（metadata 与 docs 都完成才进入路由判断）
        builder.add_edge(["metadata", "docs"], "router")
        # 条件路由：skip → 直接总结；deep → 克隆后并行分析
        builder.add_conditional_edges(
            "router", self._route_deep, {"skip": "summary", "deep": "clone"}
        )
        builder.add_edge("clone", "code")
        builder.add_edge("clone", "security")
        builder.add_edge(["code", "security"], "summary")
        builder.add_edge("summary", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile()

    # ---- 入口 ----
    def run(self, input_text: str, out_path: str | None = None) -> HealthReport:
        """执行完整体检，返回 HealthReport 对象（前端/CLI 共用）."""
        initial: AgentState = {
            "repo_input": input_text,
            "ref": None,
            "metadata": None,
            "findings": [],
            "clone_path": None,
            "clone_root": None,
            "language": None,
            "fetch_error": None,
            "summary": None,
            "report": None,
            "use_llm": self.use_llm,
        }
        result = self.graph.invoke(initial)
        report = result.get("report")
        if report is None:
            raise RuntimeError(result.get("summary") or "体检失败：未生成报告")

        from repo_agent.reporters.markdown_reporter import save_report

        if out_path is None:
            os.makedirs("reports", exist_ok=True)
            out_path = f"reports/{report.repo.full_name.replace('/', '_')}.md"
        save_report(report, out_path)
        return report
