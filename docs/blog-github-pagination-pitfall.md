<!--
草稿状态：待项目（GitHub 仓库深度体检 Agent）完整上线后再发布。
发布渠道备选：博客园 / 掘金 / 知乎 / 公众号 / 个人博客。
本文件为"备用素材"，内容已校对，发布前只需补一张封面图与结尾引流即可。
-->

# GitHub 把我坑了一次：issues 接口悄悄切到游标分页，我的统计全错了

> 一句话版：别再靠解析 `Link` header 的 `rel="last"` 来数 GitHub 的 issue / PR 了——`/issues` 端点已经游标化，这个字段正在消失，数出来全是 1。改用 Search API 的 `total_count` 才是正解。

---

## 0. 背景：我在做一个什么工具

我在做一个「GitHub 仓库深度体检 Agent」：输入一个 `owner/repo`，自动拉取元数据、文档、代码质量、安全依赖，输出一份健康报告，最后还能用 LLM 多轮追问。

其中一个维度是**社区活跃度**，需要统计仓库的开放 issue 数和开放 PR 数。最初的写法很"经典"，也是网上绝大多数教程的写法：

```python
def _count(url, **extra):
    r = client.get(url, params={"per_page": 1, **extra})
    link = r.headers.get("Link", "")
    m = re.search(r'page=(\d+)>; rel="last"', link)
    return int(m.group(1)) if m else len(r.json())
```

思路是：`per_page=1` 时，如果 `Link` header 里有 `rel="last"`，括号里那个 `page=N` 就是总数；没有就退而求其次用返回条数。

听起来没毛病，对吧？直到我盯着一个报告发呆：**某个知名大库，报告写着「开放 issue：1 个，开放 PR：86 个」**。

一个只有 1 个开放 issue、却有 86 个开放 PR 的仓库？这反常识到一眼假。

---

## 1. 抓包：GitHub 的 `Link` header 变天了

我直接用 curl 把三个端点的响应头打出来对比（无 token，2026-08 实测）：

```bash
# A. issues 接口
curl -s -D - -o /dev/null \
  "https://api.github.com/repos/psf/requests/issues?state=open&per_page=1"
# → Link: <.../issues?state=open&per_page=1&after=Y3Vyc29y...&page=2>; rel="next"
#   （注意：只有 rel="next"，没有 rel="last"）

# B. pulls 接口
curl -s -D - -o /dev/null \
  "https://api.github.com/repos/psf/requests/pulls?state=open&per_page=1"
# → Link: <...&page=2>; rel="next", <...&page=86>; rel="last"

# C. search 接口
curl -s -D - -o /dev/null \
  "https://api.github.com/search/issues?q=repo:psf/requests+is:issue+is:open&per_page=1"
# → Link: <...&page=2>; rel="next", <...&page=148>; rel="last"
```

结论很清晰：

| 端点 | 分页方式 | 还有 `rel="last"` 吗 |
|------|----------|----------------------|
| `/repos/{o}/{r}/issues` | **cursor**（`after=` 游标） | ❌ 已消失 |
| `/repos/{o}/{r}/pulls` | offset（`page=`） | ✅ 还在（暂时） |
| `/search/issues` | offset（`page=`） | ✅ 还在 |

GitHub 正在把 REST 端点从 **offset 分页**逐步迁移到 **cursor 分页**（官方文档已明确两种并存，且 cursor 用 `before`/`after`）。`/issues` 端点已经切过去了，于是 `rel="last"` 不再下发。

而我的 `_count` 一旦匹配不到 `rel="last"`，就回退到：

```python
return len(r.json())   # per_page=1 → 永远是 1
```

所以**任何有 ≥1 个开放 issue 的仓库，open_issues 都被错计成 1**。PR 那边因为 pulls 还没切，碰巧还数对——于是出现了「1 issue / 86 PR」的荒谬组合。

> 顺带一个更隐蔽的坑：`/issues` 端点会把 **PR 也当作 issue 返回**。我当初传的 `type="issue"` 根本不是 GitHub 的合法参数，过滤根本没生效。就算 `rel="last"` 还在，数出来的也是「issue + PR」的混合值。

---

## 2. 修复：统一切到 Search API 的 `total_count`

正确且省心的做法，是直接用 **Search API** 计数。`/search/issues` 会返回一个 `total_count` 字段，不用翻页、不用解析 `Link` header，而且原生支持 `is:issue` / `is:pr` 区分类型：

```python
def get_issue_metrics(self, ref):
    def _search_count(query):
        r = self._client.get("/search/issues", params={"q": query, "per_page": 1})
        if r.status_code != 200:
            return 0
        return int(r.json().get("total_count", 0))

    return IssueMetrics(
        open_issues=_search_count(f"repo:{ref.full_name} is:issue is:open"),
        closed_issues_30d=_search_count(f"repo:{ref.full_name} is:issue closed:>{since}"),
        open_prs=_search_count(f"repo:{ref.full_name} is:pr is:open"),
        merged_prs_30d=_search_count(f"repo:{ref.full_name} is:pr merged:>{since}"),
    )
```

实测同一仓库（`psf/requests`）：

- `repo:psf/requests is:issue is:open` → **148**
- `repo:psf/requests is:pr is:open` → **86**

数字合理了：issue 数量远大于 PR，符合常识。

我用单元测试把这个行为钉死：mock 掉 HTTP 客户端，断言四个指标**全部来自 `/search/issues`**、`is:issue` 与 `is:pr` 正确分流、且即便上游只返回游标分页的 `Link`（无 `rel="last"`）也不影响结果。这样以后 GitHub 再把 pulls 接口也游标化，我的统计也不会悄悄崩。

---

## 3. 给你的三个提醒

1. **别再手写 offset 分页计数**。只要你想「数总数」，优先找带 `total_count` 的接口（Search API、GraphQL 的 `totalCount`）。`rel="last"` 是 API 演进里会消失的妥协产物。
2. **`/issues` ≠ issues**。GitHub 把 PR 也算作 issue，凡是统计 issue 都必须显式加 `is:issue` 排除 PR，否则数据天然虚高。
3. **分页策略会漂移**。GitHub 官方文档现在两种分页并存，但 cursor 是终局。任何依赖 `Link` header 特定 `rel` 的代码，都该加一条「拿不到就报错/降级」的断言，而不是默默回退。

---

## 4. 写在最后

这个 bug 最有意思的地方在于：它**完全静默**。报告不会报错、不会抛异常，只是安静地写出一个明显反常识的数字。如果不是我多看了那一眼「1 issue / 86 PR」，它可能一直潜伏到上线。

这也是我把「打真实接口验证假设」写进开发流程的原因——文档会过时，但 `curl` 不会骗人。

---

*（本篇为「GitHub 仓库深度体检 Agent」开发系列的踩坑记录之一，项目完整上线后统一发布。）*
