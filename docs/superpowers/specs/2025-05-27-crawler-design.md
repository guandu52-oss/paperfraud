# 论文造假信息爬虫设计规格

## 1. 目标

从 PubPeer 和 For Better Science 定期同步论文造假相关帖子，建立可检索的本地知识库。通过 LLM 从帖子中提取造假手法，增强检测系统的 LLM 审查 prompt，实现半自动进化。

## 2. 数据源

### 2.1 PubPeer (pubpeer.com)

- **方式**：内部搜索 API（`/api/search/?q=...&token=CSRF`），需从首页提取 CSRF token，无需登录
- **获取内容**：论文标题、摘要、PubPeer ID、评论数（评论正文需登录，暂不可用）
- **增量策略**：按 source_id 去重

### 2.2 For Better Science (forbetterscience.com)

- **方式**：RSS feed（`https://forbetterscience.com/feed/`），标准 WordPress RSS 2.0
- **获取内容**：文章标题、发布日期、摘要（description）、链接、分类标签
- **增量策略**：RSS 天然支持——只拉最新 feed，按 `<guid>` 去重
- **正文获取**：RSS 只含摘要，全文需对 `<link>` URL 发 HTTP GET，用 `html.parser.HTMLParser` 提取纯文本

## 3. 架构

### 3.1 文件结构

```
paperfraud/crawler/
├── __init__.py
├── base.py              # CrawledPost, LearnedPattern dataclass
├── pubpeer_api.py        # 扩展 review/pubpeer.py：文章搜索 + 最新评论流
├── fbs_rss.py            # For Better Science RSS 解析
├── store.py             # SQLite + FTS5 全文搜索 + 同步状态
├── learner.py           # LLM 提取造假手法 → 候选规则
├── cli.py               # Typer 子命令

paperfraud_data/
├── crawler.db            # SQLite 数据库（gitignore）
├── blacklist.yaml        # 高频造假词汇 + 掩饰话术 → 注入文本检查模块（git 追踪）
└── camouflage.yaml       # 论文工厂模板句式 → 注入文本模式检测（git 追踪）
```

### 3.2 数据模型

```python
@dataclass
class CrawledPost:
    source: str          # "pubpeer" | "forbetterscience"
    source_id: str       # 站内唯一 ID（去重用）
    title: str
    url: str
    author: str
    date: str            # ISO 8601
    content: str         # 全文或摘要
    dois: list[str]      # 关联 DOI（正则 10.xxx/...）
    pmids: list[str]     # 关联 PMID（正则 8 位数字）
    fetched_at: str

@dataclass
class LearnedPattern:
    post_id: str
    category: str        # "blacklist" | "camouflage" —— LLM 分类，人工可改
    technique: str       # 造假手法描述
    detection_hint: str  # 检测建议
    severity: str        # high / medium / low
    reviewed: int        # 0=未审, 1=采纳, -1=拒绝
```

### 3.3 存储 (SQLite + FTS5)

**crawled_posts 表**：

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| source | TEXT | pubpeer / forbetterscience |
| source_id | TEXT UNIQUE | 站内唯一 ID，增量去重 |
| title | TEXT | |
| url | TEXT | |
| author | TEXT | |
| date | TEXT | ISO 8601 |
| content | TEXT | 全文 |
| dois | TEXT | JSON array |
| pmids | TEXT | JSON array |
| fetched_at | TEXT | |

**learned_patterns 表**：

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | |
| post_id | TEXT FK | 关联 crawled_posts.source_id |
| category | TEXT | blacklist / camouflage |
| technique | TEXT | 造假手法 |
| detection_hint | TEXT | 检测建议 |
| severity | TEXT | high/medium/low |
| reviewed | INTEGER | 0=未审, 1=采纳, -1=拒绝 |

**FTS5 全文索引**：`CREATE VIRTUAL TABLE posts_fts USING fts5(title, content)`

**并发安全**：数据库连接启用 WAL 模式 + 30 秒超时，cron 写入和 check 读取同时进行不锁库。

```python
def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn
```

数据库文件：`paperfraud_data/crawler.db`（与 output/ 分开，不提交 git）。

### 3.4 知识存储：两层 YAML 分类注入

**不修改 `review/prompts.py` 或检查模块源文件**。审核通过的规则按类别写入两个 YAML 文件，注入到不同防线：

**`paperfraud_data/blacklist.yaml`** — 高频造假词汇 + 生硬掩饰话术，注入**文本检查模块**（`checks/text/`）作为静态规则引擎的黑名单：

```yaml
# 高频造假词汇 + 掩饰话术黑名单
# checks/text/ 运行时读取，匹配即标红
patterns:
  - pattern: "representative images are shown"
    category: paper_mill_cliche
    technique: "论文工厂——只展示'代表性'图片，隐藏异常数据"
    severity: high
    source: "https://pubpeer.com/..."
    added: "2025-06-01"
  - pattern: "all experiments were repeated at least three times"
    category: camouflage
    technique: "用'重复三次'套话掩饰缺乏独立生物学重复"
    severity: medium
    source: "https://forbetterscience.com/..."
    added: "2025-06-01"
```

**`paperfraud_data/camouflage.yaml`** — 论文工厂模板句式，注入**文本模式检测**（`checks/text/`）做结构级匹配：

```yaml
# 论文工厂模板句式 —— 结构级匹配
# checks/text/ 运行时读取，检测句式和段落结构
patterns:
  - pattern: "data are expressed as mean ± SEM of (at least )?three independent experiments"
    category: template_sentence
    technique: "论文工厂固定模板——真实论文有多种统计表述方式"
    severity: medium
    source: "https://forbetterscience.com/..."
    added: "2025-06-03"
  - pattern: "differences were considered statistically significant at P < 0\.05"
    category: template_sentence
    technique: "单一 P 值阈值标准化表述，常见于流水线造假论文"
    severity: low
    source: "https://pubpeer.com/..."
    added: "2025-06-03"
```

**注入机制**：

- `blacklist.yaml` → `checks/text/` 模块加载为正则/关键词黑名单，命中后生成 `CheckResult(level=red/orange)`
- `camouflage.yaml` → `checks/text/` 模块加载为句式模板，用模糊匹配（编辑距离/ngram），命中后生成 `CheckResult(level=yellow/orange)`
- 两层都在 `check` 流水线的**规则引擎阶段**生效（在 LLM 审查之前），不依赖 API Key
- YAML 文件纳入 git 版本控制，知识随代码一起演进

## 4. 两条流水线

### 4.1 同步流水线（L1：上下文增强）

```
cron/launchd 每天一次
  → paperfraud crawl --sync
  → PubPeer API + FBS RSS
  → 按 source_id 去重（增量更新）
  → 写入 SQLite

paperfraud check paper.pdf
  → 提取 DOI + PMID + 标题
  → 查 crawled_posts + posts_fts
  → 命中帖子注入 LLM 审查 context
```

### 4.2 学习流水线（L2：模式提取）

```
paperfraud crawl --learn
  → 读取 crawled_posts 中尚未分析的新帖子
  → 逐篇投喂 LLM（async 并发，不是批量合并）
      输入：单篇帖子全文
      输出：{category, technique, detection_hint, severity}
        category ∈ {blacklist, camouflage}
        — blacklist: 词汇/短语级黑名单
        — camouflage: 句式/模板级匹配
  → 写入 learned_patterns 表，reviewed=0

Web UI "🧠 知识进化" 审核台
  → 人工审核候选规则
  → ✓ 采纳 → 按 category 写入对应 YAML：
      blacklist → paperfraud_data/blacklist.yaml
      camouflage → paperfraud_data/camouflage.yaml
  → ✗ 拒绝 → 标记 reviewed=-1
```

**LLM 分析策略**：

- **逐篇投喂，异步并发**（`asyncio + httpx`），不是把 10 篇合并到一个 prompt。避免 Context Window 溢出和 Lost in the Middle 张冠李戴
- **超长文章截断**：内容 > 8000 tokens 时，保留前 4000 + 后 2000 tokens（开头含摘要和结论，结尾含判定）

## 5. FBS 正文提取

用 stdlib `html.parser.HTMLParser`，不引入 BeautifulSoup：

```python
from html.parser import HTMLParser

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)
```

提取前用正则定位 `<article>` / `.entry-content` / `<main>` 区域，提取后合并空白、去重换行。

## 6. CLI

```bash
# 增量同步（每天一次）
paperfraud crawl --sync

# LLM 分析未读帖子（sync 后手动或定时跑）
paperfraud crawl --learn

# 全文搜索
paperfraud crawl --search "Western blot manipulation"

# 统计概览
paperfraud crawl --stats
# 输出：
#   PubPeer: 1,247 posts (new: 12)
#   For Better Science: 89 posts (new: 3)
#   候选规则: 5 条待审核
```

`--sync` 不需要 API Key。`--learn` 需要 `DEEPSEEK_API_KEY`。

## 7. Web UI

侧边栏导航新增页面：**"🧠 知识进化"**

- 候审规则列表：{分类标签, 手法摘要, 来源帖子链接, 严重程度, 审核状态}
- 分类标签：`blacklist`（词汇黑名单）或 `camouflage`（句式模板），人工可改
- 点击展开：左侧帖子原文，右侧 LLM 提取的检测建议
- 操作按钮：✓ 采纳 / ✗ 拒绝
- 采纳后按 category 写入对应 YAML（`blacklist.yaml` 或 `camouflage.yaml`），下次 `check` 运行时自动加载

## 8. 依赖

```toml
[project.optional-dependencies]
crawl = ["httpx"]
```

- `httpx` — HTTP 客户端（RSS 拉取 + FBS 正文抓取 + LLM 异步调用）
- RSS/XML 解析用 stdlib `xml.etree.ElementTree`
- HTML 正文提取用 stdlib `html.parser.HTMLParser`
- SQLite 用 stdlib `sqlite3`
- 异步用 stdlib `asyncio`
- 不引入 BeautifulSoup / lxml / aiohttp

`paperfraud check` 无 httpx 时仍可正常运行，爬虫功能降级为仅 PubPeer API（urllib）。

## 9. 验证

```bash
# 单元测试（mock HTTP 响应）
pytest tests/test_crawler.py -v

# 手动调试
paperfraud crawl --sync --dry-run
paperfraud crawl --search "test"

# 端到端
paperfraud crawl --sync
paperfraud crawl --learn
paperfraud check tests/fixtures/tveq.pdf --review -o output/test_crawl
# → 确认 LLM 审查上下文包含本地 SQLite 查询结果
```

## 10. 不做的

- 24h 守护进程——两个站更新频率低，每天一次 cron 足够
- 修改 prompts.py 源文件——用 blacklist.yaml + camouflage.yaml 隔离
- 10 篇批量合并 prompt——逐篇异步投喂
- 正则解析 HTML——用 html.parser.HTMLParser
- 爬取其他网站——等这两个站跑通后再扩展
- 正文 OCR/图片分析——只处理文本内容
