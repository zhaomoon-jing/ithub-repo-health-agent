"""包级工具测试（无需网络）。"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from repo_agent.analyzers.metadata_analyzer import analyze_metadata, clamp
from repo_agent.clients.github_client import GitHubClient, RepoRef, RepoRefError
from repo_agent.models import (
    CommitInfo,
    ContributorInfo,
    IssueMetrics,
    RepoMetadata,
)


class TestParseRepoRef:
    def test_owner_repo(self):
        ref = GitHubClient.parse_repo_ref("fastapi/fastapi")
        assert ref.owner == "fastapi"
        assert ref.name == "fastapi"

    def test_full_url(self):
        ref = GitHubClient.parse_repo_ref("https://github.com/langchain-ai/langgraph")
        assert ref.full_name == "langchain-ai/langgraph"

    def test_url_with_trailing(self):
        ref = GitHubClient.parse_repo_ref("https://github.com/owner/repo/tree/main")
        assert ref.full_name == "owner/repo"

    def test_invalid(self):
        try:
            GitHubClient.parse_repo_ref("not-a-repo")
            assert False, "应当抛出异常"
        except RepoRefError:
            pass


class TestMetadataAnalyzer:
    def _meta(self, **kw) -> RepoMetadata:
        now = datetime.now(timezone.utc)
        defaults = dict(
            full_name="test/foo",
            description=None,
            language="Python",
            stars=5000,
            forks=600,
            open_issues=10,
            created_at=now,
            updated_at=now,
            pushed_at=now,
            license_name="MIT",
            topics=[],
            has_issues=True,
            has_wiki=True,
            archived=False,
            homepage=None,
        )
        defaults.update(kw)
        return RepoMetadata(**defaults)

    def test_archived_repo_low_score(self):
        meta = self._meta(archived=True)
        findings = analyze_metadata(meta, [], IssueMetrics(0, 0, 0, 0), [])
        activity = next(f for f in findings if f.category == "活跃度")
        assert activity.score < 20

    def test_active_repo_high_score(self):
        now = datetime.now(timezone.utc)
        commits = [
            CommitInfo("abc1234", "fix: bug", "alice", now, ""),
            CommitInfo("def5678", "feat: x", "alice", now, ""),
            CommitInfo("ghi9012", "docs", "bob", now, ""),
        ]
        contributors = [
            ContributorInfo("alice", 100),
            ContributorInfo("bob", 80),
            ContributorInfo("carol", 60),
        ]
        meta = self._meta(stars=15000, pushed_at=now)
        findings = analyze_metadata(
            meta, commits, IssueMetrics(5, 10, 3, 8), contributors
        )
        activity = next(f for f in findings if f.category == "活跃度")
        community = next(f for f in findings if f.category == "社区/影响力")
        assert activity.score >= 70
        assert community.score >= 70

    def test_clamp(self):
        assert clamp(120) == 100
        assert clamp(-5) == 0
        assert clamp(50) == 50


class TestIssueMetricsCounting:
    """验证 get_issue_metrics 计数不依赖 Link header 的 rel="last"（游标化兼容）."""

    def _client_with(self, side_effect) -> GitHubClient:
        c = GitHubClient()
        fake = MagicMock()
        fake.get.side_effect = side_effect
        c._client = fake
        return c

    def _fake_search(self, totals: dict[str, int]):
        """构造一个 /search/issues 的 mock：按 query 子串命中 total_count."""

        def _get(url: str, params: dict | None = None):
            resp = MagicMock()
            resp.status_code = 200
            q = (params or {}).get("q", "")
            total = 0
            for key, val in totals.items():
                if key in q:
                    total = val
            resp.json.return_value = {"total_count": total}
            return resp

        return _get

    def test_all_four_use_search_api_and_distinguish_type(self):
        totals = {
            "is:issue is:open": 120,   # open issues（不含 PR）
            "is:issue closed": 30,     # 近 30 天关闭 issue
            "is:pr is:open": 45,       # open PR（不含 issue）
            "is:pr merged": 12,        # 近 30 天合并 PR
        }
        c = self._client_with(self._fake_search(totals))
        m = c.get_issue_metrics(RepoRef(owner="o", name="r"))

        assert m == IssueMetrics(120, 30, 45, 12)

        # 四个指标全部走 /search/issues，绝不调用 /issues 或 /pulls 的分页计数
        called = [call.args[0] for call in c._client.get.call_args_list]
        assert called == ["/search/issues"] * 4

    def test_cursor_only_link_header_irrelevant(self):
        """即便上游只返回游标分页的 Link（无 rel=last），也不影响计数。"""
        totals = {"is:issue is:open": 7, "is:issue closed": 0, "is:pr is:open": 3, "is:pr merged": 0}
        c = self._client_with(self._fake_search(totals))
        m = c.get_issue_metrics(RepoRef(owner="o", name="r"))
        assert m.open_issues == 7
        assert m.open_prs == 3

    def test_search_failure_returns_zero(self):
        def _get(url: str, params: dict | None = None):
            resp = MagicMock()
            resp.status_code = 403  # 限流等异常
            resp.json.return_value = {}
            return resp

        c = self._client_with(_get)
        m = c.get_issue_metrics(RepoRef(owner="o", name="r"))
        assert m == IssueMetrics(0, 0, 0, 0)
