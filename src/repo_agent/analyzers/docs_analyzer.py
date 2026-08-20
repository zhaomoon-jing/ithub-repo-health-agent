"""文档完整性分析器（D2）.

纯规则评分，不依赖 LLM：检查 README 质量 + LICENSE/CONTRIBUTING/SECURITY/CI 齐全度。
后续 W2 接入 LLM 后，可在此基础上做语义级质量评估。
"""

from __future__ import annotations

import re

from repo_agent.models import MetadataFinding, RepoMetadata

# README 中常见的关键章节/词汇（小写匹配）
_INSTALL_MARKERS = [
    "install", "installation", "quickstart", "quick start", "getting started",
    "pip install", "npm install", "requirements", "setup", "启动", "安装",
]
_EXAMPLE_MARKERS = [
    "example", "usage", "demo", "sample", "how to use", "示例", "使用",
]
_BADGE_MARKERS = [
    "![", "shields.io", "travis", "github/workflows", "codecov", "pypi",
    "badge", "ci status", "build passing",
]
_STRUCTURE_MARKERS = [
    "table of contents", "directory", "structure", "tree", "项目结构", "目录结构",
    "documentation", "docs/", "wiki",
]

_FILES_BASE_SCORES = {
    "license": 15,          # LICENSE / LICENSE.md / LICENSE.txt / COPYING
    "contributing": 5,      # CONTRIBUTING.md
    "security": 5,          # SECURITY.md
    "code_of_conduct": 0,   # 有则加分（0 基础，命中额外加）
}


def _has_marker(text: str, markers: list[str]) -> bool:
    low = text.lower()
    return any(m in low for m in markers)


def _count_code_blocks(text: str) -> int:
    """统计 ``` 围栏代码块数量（成对）."""
    fences = re.findall(r"```", text)
    return len(fences) // 2


def analyze_docs(
    meta: RepoMetadata,
    readme: str | None,
    root_files: list[str],
    has_ci: bool,
) -> MetadataFinding:
    """文档完整性评分（0-100）.

    Args:
        meta: 仓库元数据（用 license_name）。
        readme: README 原文（可能为 None）。
        root_files: 根目录文件名列表（小写）。
        has_ci: 是否存在 .github/workflows。
    """
    evidences: list[str] = []
    suggestions: list[str] = []
    score = 0.0

    # ---- README 存在性 ----
    if readme is None:
        suggestions.append("缺少 README：新用户无法快速了解项目用途与用法")
        return MetadataFinding("文档完整性", 0.0, "缺少 README，文档严重不足", evidences, suggestions)

    score += 20
    evidences.append("存在 README")

    # ---- README 长度 ----
    readme_len = len(readme)
    if readme_len >= 800:
        score += 10
        if readme_len >= 20000:
            evidences.append("README 内容充实（≥20000 字符，已截断）")
        else:
            evidences.append(f"README 内容充实（{readme_len} 字符）")
    else:
        suggestions.append(f"README 偏短（{readme_len} 字符），建议补充安装与使用说明")

    # ---- 安装/快速开始 ----
    if _has_marker(readme, _INSTALL_MARKERS):
        score += 15
        evidences.append("包含安装/快速开始指引")
    else:
        suggestions.append("README 缺少安装或快速开始章节，新用户上手成本高")

    # ---- 使用示例 ----
    code_blocks = _count_code_blocks(readme)
    if code_blocks >= 2:
        score += 15
        evidences.append(f"包含 {code_blocks} 个代码示例块")
    elif code_blocks == 1:
        score += 10
        evidences.append("包含 1 个代码示例块")
    else:
        suggestions.append("README 无代码示例，建议补充最小可用示例")

    # ---- 徽章 ----
    if _has_marker(readme, _BADGE_MARKERS):
        score += 10
        evidences.append("包含状态徽章（CI/覆盖率/版本等）")
    else:
        suggestions.append("README 无徽章，建议添加 CI/覆盖率徽章提升可信度")

    # ---- 结构/文档导航 ----
    if _has_marker(readme, _STRUCTURE_MARKERS):
        score += 10
        evidences.append("包含目录或项目结构说明")
    else:
        suggestions.append("README 缺少目录/结构说明，长文档难导航")

    # ---- LICENSE ----
    has_license_file = any(f in ("license", "license.md", "license.txt", "copying", "copying.md") for f in root_files)
    if has_license_file or meta.license_name:
        score += 15
        evidences.append(f"License: {meta.license_name or '存在 LICENSE 文件'}")
    else:
        suggestions.append("缺少 LICENSE：开源项目无许可证将阻碍社区使用与贡献")

    # ---- CONTRIBUTING ----
    has_contributing = any(f.startswith("contributing") for f in root_files)
    if has_contributing:
        score += 5
        evidences.append("存在 CONTRIBUTING.md（贡献指南）")
    else:
        suggestions.append("缺少 CONTRIBUTING.md，贡献者不知如何参与")

    # ---- SECURITY ----
    has_security = any(f.startswith("security") for f in root_files)
    if has_security:
        score += 5
        evidences.append("存在 SECURITY.md（安全政策）")
    else:
        suggestions.append("缺少 SECURITY.md，漏洞上报渠道不明确（重要项目建议补充）")

    # ---- CI 工作流 ----
    if has_ci:
        score += 10
        evidences.append("配置了 GitHub Actions 工作流（CI/CD）")
    else:
        suggestions.append("未配置 CI/CD 工作流，代码质量缺少自动门禁")

    # 治理文档缺口惩罚：缺 CONTRIBUTING / SECURITY / LICENSE 任一，扣 20 分，
    # 使分数与结论（不再判为"文档完善"）一致，避免 LLM 总评据此产出矛盾表述。
    # 用固定扣分而非封顶，保留其他维度（如 CI）的加分差异。
    governance_missing = (
        (not has_contributing) or (not has_security)
        or (not (has_license_file or meta.license_name))
    )
    if governance_missing:
        score -= 20.0
    score = round(min(max(score, 0.0), 100.0), 1)
    summary = (
        "文档完善" if score >= 90
        else "文档基本齐全" if score >= 70
        else "文档欠缺" if score >= 45
        else "文档严重不足"
    )
    return MetadataFinding("文档完整性", score, summary, evidences, suggestions)
