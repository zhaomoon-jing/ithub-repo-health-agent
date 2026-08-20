"""D8: 基于体检报告的多轮追问对话 Agent.

设计意图（贴合开发者的 对话能力）：
- 显式意图识别（意图分类）：每次提问先归类到有限意图集合，再作答。
- 上下文追踪（多轮记忆）：维护对话历史，支持「那另一方面呢」「和刚才说的比呢」式追问。
- 严格基于报告上下文，不编造报告外信息（RAG 式约束）。

LLM 复用 agents.__init__ 的 _resolve_llm（智谱优先，其次 DeepSeek，OpenAI 兼容）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum

from repo_agent.agents import _resolve_llm
from repo_agent.models import HealthReport


class QAIntent(str, Enum):
    """有限意图集合（NLU 分类目标）."""

    OVERALL = "整体评估"       # 问仓库整体健康 / 是否值得投入 / 总评
    DIMENSION = "维度细节"     # 问某个具体维度（活跃度/社区/文档/代码/安全）的详情
    IMPROVEMENT = "改进建议"   # 问怎么改 / 下一步做什么
    REPO_FACT = "仓库事实"     # 问 stars/forks/语言/license 等元数据事实
    COMPARISON = "对比追问"    # 把两个维度/两个仓库做对比
    CLARIFY = "澄清追问"       # 基于上一轮继续追问、追问细节
    OFF_TOPIC = "无关话题"     # 与本报告无关


@dataclass
class QAExchange:
    """一轮问答的结构化记录."""

    question: str
    answer: str
    intent: QAIntent
    followup: str = ""


# 系统提示词：注入报告上下文 + 意图说明 + 输出约束
_SYSTEM_PROMPT = """你是一名资深软件工程架构师助手，正在基于一份 GitHub 仓库健康体检报告回答用户的多轮追问。

# 硬性约束
- 只依据下方【报告上下文】作答，严禁编造报告中没有的信息；若报告未涉及，明确说「报告中未包含该信息」。
- 中文、简洁、专业；回答 2-4 句，必要时引用具体分数或证据。
- 支持上下文追踪：用户可能基于上一轮继续追问（如「那另一方面呢」「和刚才说的相比呢」），务必结合对话历史理解。

# 意图识别（NLU 前置步骤）
请先将用户问题归类到以下意图之一，再作答：
- 整体评估：问仓库整体健康、是否值得投入、总评结论
- 维度细节：问某个具体维度（活跃度/社区/文档/代码/安全）的详细情况
- 改进建议：问怎么改进、下一步做什么
- 仓库事实：问 stars/forks/语言/license 等元数据事实
- 对比追问：把两个维度或两个仓库做对比
- 澄清追问：基于上一轮继续追问、追问细节
- 无关话题：与本报告无关

# 输出格式（严格 JSON，不要多余文字，不要 markdown 代码块）
{"intent": "<意图标签>", "answer": "<你的回答>", "followup": "<一个可选的后续追问建议，可为空字符串>"}
"""


def _report_context(report: HealthReport) -> str:
    """把报告压成紧凑文本，作为对话上下文（避免每次重传整份报告）。"""
    lines = [
        f"仓库：{report.repo.full_name}",
        f"综合健康分：{report.overall_score}/100",
        f"总体诊断：{report.summary or '（无）'}",
        "",
    ]
    for f in report.findings:
        score = "未评估" if f.score is None else f"{f.score}/100"
        lines.append(f"【{f.category}】{score}：{f.summary}")
        for e in f.evidences[:3]:
            lines.append(f"  - 证据：{e}")
        for s in f.suggestions[:3]:
            lines.append(f"  - 建议：{s}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """从模型输出中稳健提取 JSON（兼容裸 JSON 或 ```json 包裹）。"""
    text = text.strip()
    # 优先尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试剥离 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("无法从模型输出解析出 JSON")


class RepoQA:
    """基于报告的多轮追问 Agent（意图识别 + 上下文追踪）。"""

    def __init__(self, report: HealthReport, max_history: int = 8) -> None:
        self.report = report
        self.context = _report_context(report)
        self.history: list[QAExchange] = []
        self.max_history = max_history
        cfg = _resolve_llm()
        if not cfg:
            raise RuntimeError(
                "未检测到 ZHIPU_API_KEY / DEEPSEEK_API_KEY，无法启用多轮追问"
            )
        self.api_key, self.endpoint, self.model = cfg

    def ask(self, question: str) -> QAExchange:
        """发起一轮追问，返回结构化结果并更新历史。"""
        import httpx

        messages: list[dict] = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT + "\n\n【报告上下文】\n" + self.context,
            }
        ]
        # 注入历史（最近 max_history 轮），供上下文追踪
        for ex in self.history[-self.max_history :]:
            messages.append({"role": "user", "content": ex.question})
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"intent": ex.intent.value, "answer": ex.answer},
                        ensure_ascii=False,
                    ),
                }
            )
        messages.append({"role": "user", "content": question})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = httpx.post(self.endpoint, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            obj = _extract_json(content)
        except Exception as e:  # noqa: BLE001 - 调用失败不崩，降级为友好提示
            ex = QAExchange(
                question,
                f"⚠️ 追问调用失败（{type(e).__name__}: {e}）。请确认 LLM key 有效或稍后重试。",
                QAIntent.OFF_TOPIC,
                "换一种问法再试一次？",
            )
            self.history.append(ex)
            return ex

        intent_str = str(obj.get("intent", "澄清追问"))
        answer = str(obj.get("answer", "")).strip()
        followup = str(obj.get("followup", "")).strip()
        try:
            intent = QAIntent(intent_str)
        except ValueError:
            intent = QAIntent.CLARIFY

        ex = QAExchange(question, answer, intent, followup)
        self.history.append(ex)
        return ex
