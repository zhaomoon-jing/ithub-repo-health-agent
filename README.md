# GitHub 仓库深度体检 Agent (Repo Health Agent)

输入一个 GitHub 仓库 URL，Agent 自动执行多维度深度体检，输出带证据的健康报告。

**差异化定位**：现有开源工具（RepoHealth、RepocheckAI 等）只查 GitHub API 表面元数据。
本项目 clone 代码做真实静态分析——代码质量、依赖漏洞、密钥泄露都真查，每条结论带文件行号证据。

## 功能

- [x] D1: 元数据维度体检（活跃度 / 社区影响力），输出 Markdown 报告
- [x] D2: 文档完整性分析（README / LICENSE / CONTRIBUTING / SECURITY / CI）
- [x] D3: clone 代码 + 静态分析（ruff / radon / bandit，带文件行号证据）
- [x] D4: 依赖漏洞扫描（OSV API）+ 硬编码密钥检测 → 安全维度
- [x] D5: Streamlit 可交互前端（输入 URL → 实时体检 → 5 维度可视化）
- [ ] D6: 报告追问对话（LLM 多轮问答）
- [ ] D7: LangGraph 多 Agent 编排（状态图，架构展示）

## 快速开始

```bash
# 1. 安装依赖（使用项目 venv）
pip install -e ".[dev]"

# 2. 复制环境变量（可选 GITHUB_TOKEN，不填也能跑，限流 60 次/小时）
cp .env.example .env

# 3. CLI 跑一个仓库的体检
python -m repo_agent "fastapi/fastapi"
# 或完整 URL
python -m repo_agent "https://github.com/langchain-ai/langgraph"
```

报告输出到 `reports/<owner>_<name>.md`。

### 可交互前端

```bash
# 启动 Streamlit（浏览器打开 http://localhost:8501）
streamlit run app.py
```

左侧输入 `owner/repo` 或完整 URL，点击「开始体检」即可看到总分 + 5 维度卡片（证据 + 建议）。

## 运行测试

```bash
pytest
```

## 当前体检维度

| 维度 | 检查内容 | 工具/数据源 |
|------|----------|-------------|
| 活跃度 | 提交频率、最近推送、PR 时效 | GitHub API |
| 社区/影响力 | Stars、Forks、贡献者分布 | GitHub API |
| 文档完整性 | README 质量、LICENSE/CONTRIBUTING/SECURITY/CI | GitHub API |
| 代码质量 | lint、圈复杂度、安全写法 | ruff / radon / bandit（克隆后） |
| 安全 | 依赖已知 CVE、硬编码密钥、.env 提交 | OSV API + 正则扫描（克隆后） |

## 项目结构

```
src/repo_agent/
├── __main__.py          # CLI 入口
├── app.py               # Streamlit 前端
├── models.py            # 数据模型（RepoMetadata / Finding / HealthReport）
├── agents/              # 各维度 Agent（后续 LangGraph 节点）
├── clients/             # GitHub API 客户端 + 仓库克隆器
├── analyzers/           # 评分逻辑（打分 + 证据 + 建议）
└── reporters/           # Markdown 报告生成
```

## 安全设计（评审加分点）

- 只读分析克隆的代码，绝不执行仓库内任何脚本
- 每次分析用独立临时目录，用完即清理
- 国内网络：优先 codeload tarball 下载，失败降级 git clone
- 非 Python 仓库优雅降级（静态分析/依赖扫描跳过）

## 路线图

| 周 | 内容 | 状态 |
|----|------|------|
| W1 | 元数据 → 文档 → clone 静态分析 → 安全扫描 | ✅ 完成 |
| W2 | 前端(D5✅) → 追问对话(LLM) → LangGraph 编排 | 进行中 |
