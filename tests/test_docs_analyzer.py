"""文档分析器测试（纯规则，无需网络）。"""

from datetime import datetime, timezone

from repo_agent.analyzers.docs_analyzer import analyze_docs, _count_code_blocks
from repo_agent.models import RepoMetadata

GOOD_README = """# My Project

![CI](https://github.com/owner/repo/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/foo)

## Installation

pip install foo

## Quick Start

```python
import foo
foo.run()
```

## Usage

```python
foo.analyze()
```

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)

## Structure

```
src/foo/
docs/
```
"""

MINIMAL_README = "just a name"


def _meta() -> RepoMetadata:
    now = datetime.now(timezone.utc)
    return RepoMetadata(
        full_name="test/foo",
        description=None,
        language="Python",
        stars=10,
        forks=1,
        open_issues=0,
        created_at=now,
        updated_at=now,
        pushed_at=now,
        license_name="MIT",
    )


class TestDocsAnalyzer:
    def test_no_readme(self):
        finding = analyze_docs(_meta(), None, [], False)
        assert finding.score == 0
        assert "README" in finding.summary

    def test_good_readme_full_files(self):
        files = ["readme.md", "license", "contributing.md", "security.md"]
        finding = analyze_docs(_meta(), GOOD_README, files, has_ci=True)
        assert finding.score >= 90
        assert finding.category == "文档完整性"

    def test_minimal_readme_low_score(self):
        finding = analyze_docs(_meta(), MINIMAL_README, [], False)
        assert finding.score < 40

    def test_ci_boosts_score(self):
        files = ["readme.md", "license"]
        with_ci = analyze_docs(_meta(), GOOD_README, files, has_ci=True)
        without_ci = analyze_docs(_meta(), GOOD_README, files, has_ci=False)
        assert with_ci.score > without_ci.score

    def test_count_code_blocks(self):
        assert _count_code_blocks("```\nx\n```\n```\ny\n```") == 2
        assert _count_code_blocks("no blocks") == 0
