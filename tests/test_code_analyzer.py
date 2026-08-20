"""代码质量分析器测试（本地临时项目，无需网络）."""

from __future__ import annotations

import tempfile
from pathlib import Path

from repo_agent.analyzers.code_analyzer import analyze_code_quality, detect_language

BAD_CODE = '''"""示例项目，故意包含 lint 问题、高复杂度函数和安全问题."""

import subprocess
import os

def main():
    # 故意触发 ruff F401 风格问题
    unused_var = 1
    cmd = "cat /etc/passwd"
    subprocess.run(cmd, shell=True)
    return os.getenv("SECRET")


def very_complex(a, b, c, d, e, f, g, h, i, j, k, l, m, n, o):
    """超高复杂度函数，用于触发 radon."""
    if a:
        if b:
            if c:
                if d:
                    if e:
                        if f:
                            if g:
                                if h:
                                    if i:
                                        if j:
                                            if k:
                                                if l:
                                                    if m:
                                                        if n:
                                                            if o:
                                                                return 1
    return 0


def good_fn(x):
    return x * 2
'''


def _make_bad_project() -> Path:
    tmp = tempfile.mkdtemp(prefix="repo_health_test_")
    root = Path(tmp)
    (root / "pyproject.toml").write_text("[project]\nname = 'bad'\n", encoding="utf-8")
    (root / "bad.py").write_text(BAD_CODE, encoding="utf-8")
    return root


class TestCodeAnalyzer:
    def test_detect_language_python(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "requirements.txt").write_text("x", encoding="utf-8")
            assert detect_language(root) == "python"

    def test_detect_language_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("x", encoding="utf-8")
            assert detect_language(root) == "unknown"

    def test_non_python_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text("{}", encoding="utf-8")
            assert analyze_code_quality(root, "javascript") is None

    def test_catches_real_issues(self):
        """坏项目应该检出：lint 问题 + 高复杂度 + bandit 安全问题."""
        root = _make_bad_project()
        try:
            finding = analyze_code_quality(root, "python")
            assert finding is not None
            assert finding.category == "代码质量"
            # bandit 应抓到 subprocess shell=True
            bandit_hit = any("bandit" in ev for ev in finding.evidences)
            # radon 应抓到高复杂度
            radon_hit = any("radon" in ev for ev in finding.evidences)
            assert bandit_hit, "bandit 应检测到安全问题"
            assert radon_hit, "radon 应检测到高复杂度函数"
            # 有 lint 问题应该降分
            assert finding.score < 85
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    def test_clean_project_high_score(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname = 'good'\n", encoding="utf-8")
            (root / "good.py").write_text(
                '"""Clean module."""\n\n\ndef add(a, b):\n    return a + b\n',
                encoding="utf-8",
            )
            finding = analyze_code_quality(root, "python")
            assert finding is not None
            assert finding.score >= 85
