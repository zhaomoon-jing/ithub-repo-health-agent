"""代码质量静态分析器（D3）.

三个子维度，全部基于真实工具输出（差异化的核心）：
- ruff check: lint 问题统计（E/F 规则为主）
- radon cc: 圈复杂度，找高复杂度文件
- bandit: 安全问题扫描

安全边界：只读文件，绝不执行仓库代码。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repo_agent.models import MetadataFinding


@dataclass
class StaticAnalysisResult:
    """静态分析结果."""

    language: str
    tool_supported: bool
    finding: MetadataFinding | None = None
    details: dict = field(default_factory=dict)


def detect_language(repo_path: Path) -> str:
    """根据文件分布粗判主语言（仅用于决定是否跑 Python 工具链）."""
    markers = {
        "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
        "javascript": ["package.json", "yarn.lock", "pnpm-lock.yaml"],
        "typescript": ["tsconfig.json", "package.json"],
        "java": ["pom.xml", "build.gradle"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "ruby": ["Gemfile"],
    }
    for lang, files in markers.items():
        for f in files:
            if (repo_path / f).exists():
                return lang
    # 无标记文件时，按 .py 文件数量兜底
    py_count = sum(1 for p in repo_path.rglob("*.py") if p.is_file())
    return "python" if py_count > 0 else "unknown"


def _run_tool(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    """运行工具命令，返回 (returncode, 合并输出)。失败不抛出，上层降级."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, ""


def _tool_cmd(module: str) -> list[str]:
    """用当前 Python 解释器调用已安装的 CLI 工具（兼容 venv/PATH 场景）."""
    import sys

    return [sys.executable, "-m", module]


def run_ruff(repo_path: Path) -> dict:
    """ruff check --output-format=json，统计问题数."""
    code, out = _run_tool(
        _tool_cmd("ruff") + ["check", ".", "--output-format", "json", "--quiet"],
        repo_path,
    )
    if code != 0 or not out.strip():
        return {"available": True, "issues": 0, "errors": [], "parse_ok": False}

    try:
        items = json.loads(out)
    except json.JSONDecodeError:
        return {"available": True, "issues": 0, "errors": [], "parse_ok": False}

    # 按规则码聚合，方便展示 Top 问题
    by_rule: dict[str, int] = {}
    samples: list[str] = []
    for it in items[:50]:
        rule = it.get("code", "?")
        by_rule[rule] = by_rule.get(rule, 0) + 1
        if len(samples) < 8:
            loc = it.get("location", {})
            samples.append(f"{it.get('filename', '?')}:{loc.get('row', '?')} [{rule}] {it.get('message', '')[:80]}")
    return {"available": True, "issues": len(items), "by_rule": by_rule, "samples": samples, "parse_ok": True}


def run_radon(repo_path: Path) -> dict:
    """radon cc，找平均/最大复杂度."""
    # 只看 py 文件，json 输出
    code, out = _run_tool(
        _tool_cmd("radon") + ["cc", ".", "--average", "--json", "-e", "test*", "-e", "*/test/*"],
        repo_path,
    )
    if code != 0 or not out.strip():
        return {"available": True, "parse_ok": False}

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"available": True, "parse_ok": False}

    complexes: list[tuple[str, str, float]] = []
    for file, funcs in data.items():
        for f in funcs:
            cc = f.get("complexity", 0)
            if cc >= 15:  # radon 认为 C 级以上偏高
                complexes.append((file, f.get("name", "?"), cc))
    complexes.sort(key=lambda x: -x[2])
    return {"available": True, "parse_ok": True, "complex_functions": complexes[:15], "total_files": len(data)}


def run_bandit(repo_path: Path) -> dict:
    """bandit -r -f json，安全问题数（排除测试目录 + 低置信度噪音）."""
    # --exclude 排除测试/样例目录；--skip B101(assert) 降低成熟项目噪音
    code, out = _run_tool(
        _tool_cmd("bandit")
        + [
            "-r", ".",
            "-f", "json",
            "-q",
            "--exclude", "tests,test,*/test/*,*/tests/*,examples,example,docs,documentation",
            "--skip", "B101,B311,B324,B404,B603",
        ],
        repo_path,
    )
    if not out.strip():
        return {"available": True, "issues": 0, "parse_ok": False}

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"available": True, "issues": 0, "parse_ok": False}

    results = data.get("results", [])
    samples: list[str] = []
    # 只展示 MEDIUM 以上严重度，减少噪音
    for r in results[:10]:
        if r.get("issue_severity") not in ("MEDIUM", "HIGH"):
            continue
        loc = r.get("location", {}) or {}
        samples.append(
            f"{r.get('filename', '?')}:{loc.get('line', '?')} [{r.get('test_id', '?')}/{r.get('issue_severity', '?')}] {r.get('issue_text', '')[:80]}"
        )
    return {"available": True, "issues": len(results), "samples": samples, "parse_ok": True}


def analyze_code_quality(repo_path: Path, language: str) -> MetadataFinding | None:
    """对克隆目录做静态分析，产出代码质量 finding.

    非 Python 仓库返回 None（上层跳过该维度并说明原因）。
    """
    if language != "python":
        return None

    evidences: list[str] = []
    suggestions: list[str] = []
    score = 70.0

    # 1. ruff lint
    ruff = run_ruff(repo_path)
    if ruff.get("available"):
        issues = ruff.get("issues", 0)
        evidences.append(f"ruff lint: {issues} 个问题")
        if issues == 0:
            score += 15
            evidences.append("代码通过 lint 检查（ruff 0 问题）")
        elif issues <= 20:
            score += 5
            suggestions.append(f"修复 ruff 报告的 {issues} 个 lint 问题")
        else:
            score -= min(30, issues // 5)
            suggestions.append(f"ruff 报告 {issues} 个 lint 问题，建议优先修复 E/F 规则错误")
        for s in ruff.get("samples", [])[:5]:
            evidences.append(f"  - {s}")
    else:
        suggestions.append("未安装 ruff，跳过 lint 检查")

    # 2. radon 复杂度
    radon = run_radon(repo_path)
    if radon.get("available"):
        complexes = radon.get("complex_functions", [])
        if complexes:
            score -= min(25, len(complexes) * 3)
            evidences.append(f"radon 复杂度: 发现 {len(complexes)} 个高复杂度函数(≥C)")
            for f, name, cc in complexes[:5]:
                evidences.append(f"  - {f}:{name} 复杂度 {cc:.0f}")
            suggestions.append("拆分高复杂度函数，降低认知负担")
        else:
            evidences.append("radon 复杂度: 无显著高复杂度函数")
            score += 10
    else:
        suggestions.append("未安装 radon，跳过复杂度检查")

    # 3. bandit 安全
    bandit = run_bandit(repo_path)
    if bandit.get("available"):
        issues = bandit.get("issues", 0)
        if issues > 0:
            # 按严重度梯度扣分：少量中高危问题扣得多，大量低危问题扣得少
            if issues >= 30:
                score -= 30
            elif issues >= 10:
                score -= 20
            elif issues >= 3:
                score -= 10
            else:
                score -= 5
            evidences.append(f"bandit 安全扫描: {issues} 个潜在安全问题（已排除测试目录）")
            for s in bandit.get("samples", [])[:5]:
                evidences.append(f"  - {s}")
            suggestions.append("修复 bandit 报告的安全问题（优先高风险项）")
        else:
            evidences.append("bandit 安全扫描: 未发现已知模式的安全问题")
            score += 10
    else:
        suggestions.append("未安装 bandit，跳过安全扫描")

    score = max(0.0, min(100.0, score))
    summary = (
        "代码质量优秀" if score >= 85
        else "代码质量良好" if score >= 70
        else "代码质量一般" if score >= 50
        else "代码质量堪忧"
    )
    return MetadataFinding("代码质量", round(score, 1), summary, evidences, suggestions)
