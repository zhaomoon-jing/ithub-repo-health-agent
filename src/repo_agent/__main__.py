"""CLI 入口：python -m repo_agent 'owner/repo'"""

from __future__ import annotations

import argparse
import sys

from repo_agent.agents import Pipeline
from repo_agent.clients.github_client import RepoRefError


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="repo-health-agent",
        description="GitHub 仓库深度体检 Agent",
    )
    parser.add_argument(
        "repo",
        help="仓库引用，如 'fastapi/fastapi' 或完整 URL",
    )
    parser.add_argument(
        "-o", "--out",
        help="报告输出路径（默认 reports/<owner>_<name>.md）",
    )
    parser.add_argument(
        "--token",
        help="GitHub Token（也可用环境变量 GITHUB_TOKEN）",
    )
    parser.add_argument(
        "--skip-deep",
        action="store_true",
        help="跳过 clone 深度分析（只用 API 数据，更快）",
    )
    args = parser.parse_args()

    try:
        pipeline = Pipeline(token=args.token, skip_deep=args.skip_deep)
        out = pipeline.run(args.repo, args.out)
        print(f"✅ 体检完成，报告已生成: {out}")
        return 0
    except RepoRefError as e:
        print(f"❌ 输入错误: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - CLI 层兜底
        print(f"❌ 执行失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
