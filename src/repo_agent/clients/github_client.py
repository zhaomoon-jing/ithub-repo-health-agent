"""GitHub 客户端（网络请求 + 仓库引用解析）."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx

from repo_agent.models import (
    CommitInfo,
    ContributorInfo,
    IssueMetrics,
    RepoMetadata,
    RepoRef,
)

GITHUB_API = "https://api.github.com"
_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/?")
_TOKEN_PATTERNS = [r"gh[pousr]_[A-Za-z0-9]{36,255}", r"github_pat_[A-Za-z0-9_]{22,}"]


class RepoRefError(ValueError):
    """仓库引用非法."""


class GitHubClient:
    """封装 GitHub REST API 调用（只读）。"""

    def __init__(self, token: str | None = None, timeout: float = 15.0) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.timeout = timeout
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.Client(base_url=GITHUB_API, headers=headers, timeout=self.timeout)

    @staticmethod
    def parse_repo_ref(input_text: str) -> RepoRef:
        """把 'owner/repo' 或完整 URL 解析成 RepoRef.

        Raises:
            RepoRefError: 无法解析时抛出.
        """
        text = input_text.strip().strip("/")
        if not text:
            raise RepoRefError("仓库引用不能为空")

        # 完整 URL: https://github.com/owner/repo
        if text.startswith(("http://", "https://", "git@")):
            if "github.com" not in text:
                raise RepoRefError(f"仅支持 GitHub 仓库，收到: {input_text}")
            m = _URL_RE.search(text)
            if not m:
                raise RepoRefError(f"无法从 URL 解析出 owner/repo: {input_text}")
            return RepoRef(owner=m.group(1), name=m.group(2))

        # 简写: owner/repo
        parts = [p for p in text.split("/") if p]
        if len(parts) == 2:
            return RepoRef(owner=parts[0], name=parts[1])

        raise RepoRefError(
            f"无法解析仓库引用: {input_text}。请使用 'owner/repo' 或完整 URL"
        )

    def get_repo(self, ref: RepoRef) -> RepoMetadata:
        """拉取仓库核心元数据."""
        resp = self._client.get(f"/repos/{ref.full_name}")
        resp.raise_for_status()
        data = resp.json()
        return RepoMetadata(
            full_name=data["full_name"],
            description=data.get("description"),
            language=data.get("language"),
            stars=data["stargazers_count"],
            forks=data["forks_count"],
            open_issues=data["open_issues_count"],
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            pushed_at=_parse_dt(data.get("pushed_at")),
            license_name=(data.get("license") or {}).get("spdx_id"),
            topics=data.get("topics") or [],
            has_issues=data.get("has_issues", False),
            has_wiki=data.get("has_wiki", False),
            archived=data.get("archived", False),
            homepage=data.get("homepage"),
        )

    def get_recent_commits(self, ref: RepoRef, per_page: int = 5) -> list[CommitInfo]:
        """拉取最近提交."""
        resp = self._client.get(
            f"/repos/{ref.full_name}/commits", params={"per_page": per_page}
        )
        resp.raise_for_status()
        commits = []
        for item in resp.json():
            commit = item.get("commit", {})
            author = commit.get("author") or {}
            # 优先取 GitHub 账号 login，匿名提交可能只有 name
            login = item.get("author") or {}
            author_name = login.get("login") if isinstance(login, dict) else None
            author_name = author_name or author.get("name") or "unknown"
            commits.append(
                CommitInfo(
                    sha=item.get("sha", "")[:8],
                    message=(commit.get("message") or "").split("\n")[0],
                    author=author_name,
                    date=_parse_dt(author.get("date")),
                    url=item.get("html_url", ""),
                )
            )
        return commits

    def get_top_contributors(self, ref: RepoRef, per_page: int = 5) -> list[ContributorInfo]:
        """拉取 Top 贡献者（仅统计能取到的场景，兼容匿名请求限流）."""
        try:
            resp = self._client.get(
                f"/repos/{ref.full_name}/contributors",
                params={"per_page": per_page},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            # 贡献者接口限流较严，取不到就返回空，不阻塞主流程
            return []
        return [
            ContributorInfo(login=item.get("login", "unknown"), contributions=item.get("contributions", 0))
            for item in resp.json()
        ]

    def get_issue_metrics(self, ref: RepoRef) -> IssueMetrics:
        """统计 Issue/PR 数量与近 30 天处理时效.

        计数策略（重要）：四个指标**统一走 Search API**，返回 total_count 直接得总数。

        为什么不再解析 Link header 的 rel="last"？
        - GitHub 正逐步把 REST 端点从 offset 分页迁移到 cursor 分页。
          实测 ``/repos/{o}/{r}/issues?state=open`` 的 Link header 已只返回
          ``rel="next"`` + ``after=`` 游标，**不再有 rel="last"**，导致无法用总页数
          推断总数；代码中 ``re.search(r'page=(\\d+)>; rel="last"')`` 匹配不到时会
          回退到 ``len(r.json())``（per_page=1 时为 1），于是任何有 ≥1 个 open issue
          的仓库都会被错计成 1。
        - 即便能解析，``/issues`` 端点会把 PR 也当作 issue 返回，必须靠
          ``is:issue`` / ``is:pr`` 区分；Search API 原生支持这种过滤，计数最准确。
        - 因此这里全部用 Search API 计数，彻底规避 cursor 化带来的兼容性问题
          （pulls 接口目前仍返回 rel="last"，但统一走 Search API 可一劳永逸）。
        """
        from datetime import datetime, timedelta, timezone

        since_iso = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

        def _search_count(query: str) -> int:
            """用 Search API 精确计数，失败返回 0.

            Search API 直接返回 total_count，无需翻页/解析 Link header，
            也不受 cursor 分页影响；支持 is:issue / is:pr 精确区分 issue 与 PR。
            """
            try:
                r = self._client.get(
                    "/search/issues",
                    params={"q": query, "per_page": 1},
                )
                if r.status_code != 200:
                    return 0
                return int(r.json().get("total_count", 0))
            except (httpx.HTTPError, ValueError):
                return 0

        return IssueMetrics(
            open_issues=_search_count(f"repo:{ref.full_name} is:issue is:open"),
            closed_issues_30d=_search_count(f"repo:{ref.full_name} is:issue closed:>{since_iso}"),
            open_prs=_search_count(f"repo:{ref.full_name} is:pr is:open"),
            merged_prs_30d=_search_count(f"repo:{ref.full_name} is:pr merged:>{since_iso}"),
        )

    def get_readme_raw(self, ref: RepoRef, max_chars: int = 20000) -> str | None:
        """拉取 README 原始内容（文档维度 Agent 用），失败返回 None."""
        try:
            resp = self._client.get(
                f"/repos/{ref.full_name}/readme", headers={"Accept": "application/vnd.github.raw+json"}
            )
            if resp.status_code != 200:
                return None
            return resp.text[:max_chars]
        except httpx.HTTPError:
            return None

    def get_root_files(self, ref: RepoRef) -> list[str]:
        """列出仓库根目录文件名（小写）。失败返回空列表，不阻塞主流程."""
        try:
            resp = self._client.get(f"/repos/{ref.full_name}/contents/")
            if resp.status_code != 200:
                return []
            items = resp.json()
            # 防御：接口在异常场景（空仓库/重定向/错误体）可能返回 dict 而非 list，
            # 直接遍历会把 key（str）当条目，触发 'str' object has no attribute 'get'。
            if not isinstance(items, list):
                return []
            return [
                item.get("name", "").lower()
                for item in items
                if isinstance(item, dict) and item.get("type") == "file"
            ]
        except httpx.HTTPError:
            return []

    def has_ci_workflows(self, ref: RepoRef) -> bool:
        """检测是否存在 .github/workflows（CI/CD 配置）."""
        try:
            resp = self._client.get(f"/repos/{ref.full_name}/contents/.github/workflows")
            if resp.status_code == 200 and isinstance(resp.json(), list):
                return len(resp.json()) > 0
            return False
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, **kwargs) -> httpx.Response:
        """直接请求任意 URL（复用统一客户端配置，供 cloner 等模块使用）."""
        return self._client.get(url, **kwargs)

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def _parse_dt(value: str | None) -> datetime:
    """解析 GitHub ISO8601 时间字符串；解析失败返回 epoch."""
    from datetime import datetime

    if not value:
        return datetime.fromtimestamp(0)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0)
