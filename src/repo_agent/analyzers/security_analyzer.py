"""D4: 安全分析 —— 依赖漏洞(OSV) + 硬编码密钥扫描.

与 code_analyzer 的区别:
- code_analyzer(bandit) 扫的是「代码里的安全写法」(如 eval/不安全反序列化)
- 本模块扫的是「供应链」(第三方依赖已知 CVE) 与「凭证泄露」(硬编码密钥)
两个维度互补，构成完整安全视图。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from repo_agent.models import MetadataFinding

# ---- 硬编码密钥检测规则 ----
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9/+=]{40}['\"]")),
    ("Private Key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("Generic API Key", re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]")),
    ("Hardcoded Password", re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
]

# 扫描时跳过的目录
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox"}
# 扫描的文本扩展名
_TEXT_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".env", ".txt", ".yaml", ".yml",
    ".json", ".toml", ".cfg", ".ini", ".sh", ".md", ".go", ".java", ".rb",
    ".php", ".cs", ".c", ".cpp", ".h",
}
_OSV_URL = "https://api.osv.dev/v1/query"
_OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"


def parse_dependencies(repo_path: Path) -> list[tuple[str, str | None]]:
    """解析仓库内的 Python 依赖声明，返回 [(包名, 版本), ...].

    支持: requirements*.txt(含 -r 嵌套) / pyproject.toml(Poetry + PEP621) / Pipfile.lock
    仅取「生产依赖」，跳过 name 含 test/dev 的文件与 dev-dependencies。
    """
    deps: list[tuple[str, str | None]] = []
    seen_files: set[Path] = set()

    for req in repo_path.rglob("requirements*.txt"):
        low = req.name.lower()
        if "test" in low or "dev" in low:
            continue
        deps += _parse_requirements(req, repo_path, seen_files)

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        deps += _parse_pyproject(pyproject)

    pipfile_lock = repo_path / "Pipfile.lock"
    if pipfile_lock.exists():
        deps += _parse_pipfile_lock(pipfile_lock)

    setup_py = repo_path / "setup.py"
    if setup_py.exists():
        deps += _parse_setup_py(setup_py)
    setup_cfg = repo_path / "setup.cfg"
    if setup_cfg.exists():
        deps += _parse_setup_cfg(setup_cfg)

    # 去重(保留第一个出现的版本)
    merged: dict[str, str | None] = {}
    for name, ver in deps:
        if name and name not in merged:
            merged[name] = ver
    return list(merged.items())


def _parse_requirements(path: Path, root: Path, seen: set[Path]) -> list[tuple[str, str | None]]:
    if path in seen or not path.exists():
        return []
    seen.add(path)
    out: list[tuple[str, str | None]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            child = (path.parent / line.split(None, 1)[1].strip()).resolve()
            out += _parse_requirements(child, root, seen)
            continue
        # 去掉环境标记、选项
        name = re.split(r"[<>=!~ \[\];#]", line, maxsplit=1)[0].strip()
        m = re.search(r"([=~<>!]=?)\s*([0-9][A-Za-z0-9.*+!-]*)", line)
        ver = m.group(2) if m else None
        if name and re.match(r"^[A-Za-z0-9._-]+$", name):
            out.append((name, ver))
    return out


def _parse_pyproject(path: Path) -> list[tuple[str, str | None]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    deps: list[tuple[str, str | None]] = []
    # 极简 TOML 解析(避免额外依赖): 提取 [tool.poetry.dependencies] 与 [project]
    section = None
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped
            in_project = section in ("[project]", "[tool.poetry.dependencies]")
            continue
        if not in_project:
            continue
        if "=" not in stripped or stripped.startswith("#"):
            continue
        # [project] 形如 dependencies = ["flask>=2.0", "requests"]
        if "dependencies" in stripped and stripped.startswith("dependencies"):
            # 整段数组同行或多行, 简单提取引号内容
            for m in re.finditer(r"['\"]([A-Za-z0-9._-]+)\s*(?:[<>=!~][^'\"]*)?['\"]", stripped):
                name = m.group(1)
                vm = re.search(rf"{re.escape(name)}\s*(?:[<>=!~]\s*([0-9][A-Za-z0-9.*+!-]*))", stripped)
                deps.append((name, vm.group(1) if vm else None))
            continue
        # [tool.poetry.dependencies] 形如 flask = "^2.0"
        if stripped.startswith("python") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
        if key in ("version",):
            continue
        vm = re.search(r"([0-9][A-Za-z0-9.*+!-]*)", val)
        deps.append((key, vm.group(1) if vm else None))
    return deps


def _parse_pipfile_lock(path: Path) -> list[tuple[str, str | None]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[str, str | None]] = []
    for section in ("default", "develop"):
        for name, meta in data.get(section, {}).items():
            ver = meta.get("version", "").lstrip("^=~<>")
            out.append((name, ver or None))
    return out


def query_osv(deps: list[tuple[str, str | None]], timeout: int = 30) -> list[tuple[str, str | None, list]]:
    """批量查询 OSV，返回 [(包名, 版本, vulns列表), ...] 仅含命中项.

    优先用 querybatch 端点, 失败降级为逐个 /query。
    """
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v}
               for n, v in deps if v]
    if not queries:
        return []

    results: list[tuple[str, str | None, list]] = []
    try:
        r = httpx.post(_OSV_BATCH_URL, json={"queries": queries}, timeout=timeout)
        if r.status_code == 200:
            resp_list = r.json().get("results", [])
            for (n, v), resp in zip(deps, resp_list):
                vulns = resp.get("vulns", [])
                if vulns:
                    results.append((n, v, vulns))
            return results
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        pass

    # 降级: 逐个查询
    for n, v in deps:
        if not v:
            continue
        try:
            r = httpx.post(_OSV_URL, json={"package": {"name": n, "ecosystem": "PyPI"}, "version": v}, timeout=10)
            if r.status_code == 200:
                vulns = r.json().get("vulns", [])
                if vulns:
                    results.append((n, v, vulns))
        except (httpx.HTTPError, json.JSONDecodeError):
            continue
    return results


def scan_secrets(repo_path: Path, max_file_bytes: int = 500_000) -> list[tuple[str, int, str]]:
    """扫描仓库内疑似硬编码密钥, 返回 [(相对路径, 行号, 规则名), ...]."""
    findings: list[tuple[str, int, str]] = []
    for p in repo_path.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in _TEXT_EXTS and p.name != ".env":
            continue
        if p.stat().st_size > max_file_bytes:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(repo_path))
        for ln, line in enumerate(text.splitlines(), 1):
            for name, pat in SECRET_PATTERNS:
                if pat.search(line):
                    findings.append((rel, ln, name))
                    break
    return findings


def analyze_security(repo_path: Path, language: str | None) -> MetadataFinding:
    """汇总安全维度: 依赖漏洞(仅 Python) + 密钥泄露(通用)."""
    evidences: list[str] = []
    suggestions: list[str] = []
    score = 100.0

    # 1) 依赖漏洞(OSV)
    if language == "python":
        deps = parse_dependencies(repo_path)
        evidences.append(f"解析到 {len(deps)} 个 PyPI 依赖")
        if deps:
            vuln_results = query_osv(deps)
            if vuln_results:
                total = sum(len(v) for _, _, v in vuln_results)
                score -= min(40, total * 8)
                evidences.append(f"⚠️ {len(vuln_results)} 个依赖存在已知漏洞（共 {total} 条记录）")
                for n, v, vulns in vuln_results[:10]:
                    ids = [x.get("id", "?") for x in vulns[:3]]
                    evidences.append(f"  - {n}=={v}: {', '.join(ids)}")
                    suggestions.append(f"升级 {n} 以修复已知漏洞（{', '.join(ids)}）")
            else:
                evidences.append("依赖未发现已知漏洞（OSV 数据库）")
        else:
            evidences.append("未发现可解析的 Python 依赖声明")
    else:
        evidences.append(f"仓库主语言为 {language or '未知'}，依赖漏洞扫描目前仅支持 PyPI 生态，该项未评估")

    # 2) 硬编码密钥
    secrets = scan_secrets(repo_path)
    if secrets:
        score -= min(45, len(secrets) * 15)
        evidences.append(f"⚠️ 发现 {len(secrets)} 处疑似硬编码密钥/凭证")
        for rel, ln, name in secrets[:10]:
            evidences.append(f"  - {rel}:{ln} [{name}]")
            suggestions.append(f"移除 {rel}:{ln} 的硬编码凭证，改用环境变量或密钥管理服务")
    else:
        evidences.append("未发现硬编码密钥/凭证")

    # 3) 提交敏感 .env
    env_files = [str(p.relative_to(repo_path)) for p in repo_path.rglob(".env") if p.is_file()]
    if env_files:
        score -= 10
        for e in env_files:
            evidences.append(f"⚠️ 仓库包含 .env 文件（敏感配置不应提交）: {e}")
            suggestions.append(f"将 {e} 加入 .gitignore，提交 .env.example 模板替代")

    score = max(0.0, min(100.0, score))
    if score >= 90:
        summary = "安全状况良好"
    elif score >= 70:
        summary = "存在安全隐患，建议修复"
    else:
        summary = "存在严重安全问题，需尽快处理"
    return MetadataFinding("安全", round(score, 1), summary, evidences, suggestions)


def _extract_name_ver(spec: str) -> tuple[str | None, str | None]:
    """从依赖声明(如 'flask>=2.2' / 'requests; python_version<"3.8"')提取包名+版本.

    先去掉分号后的环境标记，避免把 python_version 的版本误当包版本。
    """
    spec = spec.strip().split(";", 1)[0].strip()
    if not spec or spec.startswith("#"):
        return None, None
    m = re.match(r"([A-Za-z][A-Za-z0-9._-]*)\s*([<>=!~]=?)\s*([0-9][A-Za-z0-9.*+!-]*)", spec)
    if m:
        return m.group(1), m.group(3)
    m2 = re.match(r"([A-Za-z][A-Za-z0-9._-]*)", spec)
    return (m2.group(1), None) if m2 else (None, None)


def _parse_setup_py(path: Path) -> list[tuple[str, str | None]]:
    """解析 setup.py 的 install_requires 列表.

    逐行提取（而非引号配对），避免 install_requires 内嵌单引号
    （如 'python_version<"3.10"'）破坏配对导致漏解析。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    deps: list[tuple[str, str | None]] = []
    m = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip().strip(",").strip().strip("'\"")
            if not line or line.startswith("#"):
                continue
            name, ver = _extract_name_ver(line)
            if name:
                deps.append((name, ver))
    return deps


def _parse_setup_cfg(path: Path) -> list[tuple[str, str | None]]:
    """解析 setup.cfg 的 [options] install_requires 多行块."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    deps: list[tuple[str, str | None]] = []
    m = re.search(r"\[options\][^\[]*install_requires\s*=\s*\n((?:\s+[^\[\n]+\n)+)", text)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                name, ver = _extract_name_ver(line)
                if name:
                    deps.append((name, ver))
    return deps
