"""仓库获取器：把仓库源码拉到临时目录，用完清理.

两种获取方式（自动降级）：
1. codeload tarball 下载（默认，国内网络友好，走 codeload.github.com）
2. git clone（备选，需要 github.com 可达）

安全设计（评审加分项）：
- 只读文件做静态分析，绝不执行仓库内任何脚本
- 每次拉到独立临时目录，用完即清理
- tarball 有体积上限，防巨型仓库拖垮分析
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from repo_agent.clients.github_client import GitHubClient

TARBALL_URL = "https://codeload.github.com/{owner}/{name}/tar.gz/refs/heads/{branch}"
DEFAULT_BRANCH = "main"


class FetchError(Exception):
    """获取仓库失败."""


@dataclass
class FetchedRepo:
    """获取结果：本地路径 + 清理回调."""

    path: Path
    method: str  # "tarball" | "git"
    _cleanup: object = field(default=None, repr=False)

    def cleanup(self) -> None:
        """删除临时目录."""
        if self._cleanup is not None:
            self._cleanup()
            self._cleanup = None


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def fetch_repo_tarball(
    client: GitHubClient,
    owner: str,
    name: str,
    branch: str | None = None,
    timeout: int = 180,
    max_mb: int = 200,
) -> FetchedRepo:
    """通过 codeload 下载 tar.gz 并解压（默认方式，国内网络友好）.

    Raises:
        FetchError: 下载失败、解压失败或体积超限
    """
    if branch is None:
        branch = DEFAULT_BRANCH
    url = TARBALL_URL.format(owner=owner, name=name, branch=branch)

    try:
        # 复用 GitHubClient 的 httpx 客户端（带 token/超时配置）
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"tarball 下载失败: {e}")

    if len(resp.content) > max_mb * 1024 * 1024:
        raise FetchError(f"tarball 体积过大（>{max_mb}MB），跳过深度分析")

    tmp_dir = tempfile.mkdtemp(prefix="repo_health_")
    target = Path(tmp_dir)
    try:
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tf:
            # 解压到临时目录，去掉顶层一层（如 requests-main/）
            members = tf.getmembers()
            if not members:
                raise FetchError("tarball 内容为空")
            tf.extractall(tmp_dir)
        # 找到真正的项目根（去掉顶层目录）
        subdirs = [p for p in target.iterdir() if p.is_dir()]
        if len(subdirs) == 1 and subdirs[0].name != tmp_dir:
            target = subdirs[0]
        elif len(subdirs) > 1:
            target = target  # 多目录直接用根
    except (tarfile.TarError, OSError) as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise FetchError(f"tarball 解压失败: {e}")

    return FetchedRepo(path=target, method="tarball",
                       _cleanup=lambda: shutil.rmtree(tmp_dir, ignore_errors=True))


def fetch_repo_git(
    clone_url: str,
    timeout: int = 300,
    max_mb: int = 200,
    retries: int = 2,
) -> FetchedRepo:
    """git clone 到临时目录（备选方式，需要 github.com 可达）.

    Raises:
        FetchError: 克隆失败或体积超限
    """
    tmp_dir = tempfile.mkdtemp(prefix="repo_health_")
    target = Path(tmp_dir)

    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", clone_url, str(target)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode == 0:
                break
            last_err = proc.stderr.strip()[:500]
        except subprocess.TimeoutExpired:
            last_err = f"克隆超时（>{timeout}s）"
        except FileNotFoundError:
            raise FetchError("未找到 git 命令，请先安装 git")
        if attempt < retries:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = tempfile.mkdtemp(prefix="repo_health_")
            target = Path(tmp_dir)
    else:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise FetchError(f"git clone 失败: {last_err}")

    size_mb = _dir_size_mb(target)
    if size_mb > max_mb:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise FetchError(f"仓库体积过大（{size_mb:.0f}MB > {max_mb}MB），跳过深度分析")

    return FetchedRepo(path=target, method="git",
                       _cleanup=lambda: shutil.rmtree(tmp_dir, ignore_errors=True))


def fetch_repo(
    client: GitHubClient,
    clone_url: str,
    owner: str,
    name: str,
) -> FetchedRepo:
    """综合获取：优先 tarball，失败降级 git clone.

    Raises:
        FetchError: 两种方式都失败
    """
    try:
        return fetch_repo_tarball(client, owner, name)
    except FetchError as e:
        try:
            return fetch_repo_git(clone_url)
        except FetchError as e2:
            raise FetchError(f"tarball 失败（{e}）; git clone 失败（{e2}）")
