"""D4 测试：依赖解析 / 密钥扫描 / 安全评分."""
import tempfile
from pathlib import Path

import pytest

from repo_agent.analyzers import security_analyzer as sa
from repo_agent.analyzers.security_analyzer import (
    analyze_security,
    parse_dependencies,
    scan_secrets,
)


def _mk_repo(files: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def test_parse_requirements_nested():
    d = _mk_repo({
        "requirements.txt": "flask==2.2.0\nrequests>=2.0\n# comment\n-r base.txt\n",
        "base.txt": "numpy==1.24.0\n",
    })
    deps = parse_dependencies(d)
    names = {n for n, _ in deps}
    assert {"flask", "requests", "numpy"} <= names
    ver = dict(deps)
    assert ver["flask"] == "2.2.0"
    assert ver["numpy"] == "1.24.0"


def test_parse_pyproject_poetry():
    toml = """
[tool.poetry.dependencies]
python = "^3.10"
flask = "^2.2"
requests = "*"

[tool.poetry.group.dev.dependencies]
pytest = "^7"
"""
    d = _mk_repo({"pyproject.toml": toml})
    deps = parse_dependencies(d)
    names = {n for n, _ in deps}
    assert {"flask", "requests"} <= names
    ver = dict(deps)
    assert ver["flask"] == "2.2"


def test_parse_setup_py_with_env_marker():
    setup = '''
from setuptools import setup
setup(
    name="x",
    install_requires=[
        "requests>=2.0",
        "flask; python_version<'3.10'",
        "click",
    ],
)
'''
    d = _mk_repo({"setup.py": setup})
    deps = parse_dependencies(d)
    names = {n for n, _ in deps}
    assert {"requests", "flask", "click"} <= names
    ver = dict(deps)
    assert ver["requests"] == "2.0"
    assert ver["flask"] is None  # 环境标记版本不应误提取为依赖
    assert "3.10" not in names  # 环境标记版本号不应误当包名


def test_scan_secrets_detects():
    d = _mk_repo({
        "app.py": 'aws_key = "AKIAIOSFODNN7EXAMPLE"\nnormal = 1\n',
        "config.py": 'password = "supersecret123"\n',
        "safe.py": "x = 1\ny = 2\n",
    })
    secrets = scan_secrets(d)
    rels = {r for r, _, _ in secrets}
    assert "app.py" in rels
    assert "config.py" in rels
    assert "safe.py" not in rels


def test_analyze_security_with_vuln(monkeypatch):
    d = _mk_repo({"requirements.txt": "flask==2.2.0\n"})
    monkeypatch.setattr(
        sa, "query_osv",
        lambda deps, timeout=30: [("flask", "2.2.0", [{"id": "GHSA-xxxx"}])],
    )
    f = analyze_security(d, "python")
    assert f.category == "安全"
    assert f.score < 100
    assert any("flask" in e for e in f.evidences)
    assert any("升级 flask" in s for s in f.suggestions)


def test_analyze_security_non_python_skips_deps():
    d = _mk_repo({"package.json": '{"name":"x"}'})
    f = analyze_security(d, "javascript")
    # 依赖漏洞项标记未评估，但密钥扫描仍执行
    assert any("仅支持 PyPI" in e for e in f.evidences)
    assert f.score >= 90  # 无漏洞、无密钥
