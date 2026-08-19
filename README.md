# GitHub 仓库深度体检 Agent (Repo Health Agent)

输入一个 GitHub 仓库 URL，Agent 自动执行多维度深度体检，输出带证据的健康报告。

**差异化定位**：现有开源工具（RepoHealth、RepocheckAI 等）只查 GitHub API 表面元数据。
本项目 clone 代码做真实静态分析——代码质量、依赖漏洞、密钥泄露都真查，每条结论带文件行号证据。

## 功能

- [x] D1: 元数据维度体检（活跃度 / 社区影响力），输出 Markdown 报告
- [ ] D2: 文档完整性分析（README / LICENSE / CONTRIBUTING）
- [ ] D3: clone 代码 + 静态分析（ruff / radon / bandit）
- [ ] D4: 依赖漏洞扫描（OSV API）+ 密钥泄露检测
- [ ] D5: LangGraph 多 Agent 编排 + 条件分支 + 加权评分
- [ ] D6: 报告追问对话 + SQLite 历史记录
- [ ] D7: Streamlit 界面 + 演示录屏

## 快速开始

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 复制环境变量（可选 GITHUB_TOKEN，不填也能跑，限流 60 次/小时）
cp .env.example .env

# 3. 跑一个仓库的体检
python -m repo_agent "fastapi/fastapi"
# 或完整 URL
python -m repo_agent "https://github.com/langchain-ai/langgraph"
```

报告输出到 `reports/<owner>_<name>.md`。

## 运行测试

```bash
pytest
```

## 项目结构

```
src/repo_agent/
├── __main__.py          # CLI 入口
├── models.py            # 数据模型（RepoMetadata / Finding / HealthReport）
├── agents/              # 各维度 Agent（后续 LangGraph 节点）
├── clients/             # GitHub API 客户端
├── analyzers/           # 评分逻辑（打分 + 证据 + 建议）
└── reporters/           # Markdown 报告生成
```

## 路线图

| 周 | 内容 | 状态 |
|----|------|------|
| W1 | 元数据 → 文档 → clone 静态分析 | 进行中 |
| W2 | 安全扫描 → LangGraph 编排 → 追问 → 前端 | 待开始 |
