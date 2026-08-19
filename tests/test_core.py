"""包级工具测试（无需网络）。"""

from datetime import datetime, timezone

from repo_agent.analyzers.metadata_analyzer import analyze_metadata, clamp
from repo_agent.clients.github_client import GitHubClient, RepoRefError
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
