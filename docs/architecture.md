# paperfraud 架构文档

## 数据流总览

```
DOI/PDF 输入
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│                      CLI (cli.py)                        │
│  解析参数 → 构建 Config → 调度全流程                      │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│                   Parser (parser/)                        │
│  PDF → ParsedPaper (full_text + sections + images)       │
│  ┌────────────────────────────────────────────────────┐  │
│  │ PyMuPDF (默认)     │  GROBID (可选, --grobid)       │  │
│  │ 准确率 ~60%        │  准确率 ~85%, 需 Docker        │  │
│  │ 手写正则分段       │  CRF 模型识别 IMRaD            │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
    │  ParsedPaper
    ▼
┌──────────────────────────────────────────────────────────┐
│                 Checks (checks/)                          │
│  ParsedPaper → [CheckResult, ...]                        │
│                                                          │
│  numbers/          images/         text/                 │
│  ├─ py_statcheck   ├─ lut          ├─ blacklist          │
│  ├─ grim           ├─ clone_detect ├─ pvalue_camouflage  │
│  ├─ digit_pref     └─ ela          │                     │
│  └─ arithmetic                     logic/ (Phase 2)      │
│                                    bioinfo/ (Phase 2)    │
│  并行执行 (ThreadPoolExecutor, max_workers=8)             │
└──────────────────────────────────────────────────────────┘
    │  [CheckResult, ...]
    ▼
┌──────────────────────────────────────────────────────────┐
│                Aggregator (report/aggregator.py)          │
│  汇总 → {overall_level, red_count, orange_count, ...}    │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│               Formatter (report/formatter.py)             │
│  terminal (rich 表格) / JSON / Markdown                   │
└──────────────────────────────────────────────────────────┘
```

## 核心数据结构

### ParsedPaper (`base.py`)
解析后的论文中间表示，所有检查模块共享：

```
ParsedPaper
├─ file_path: Path          # PDF 路径
├─ doi: str | None          # DOI（DOI 输入模式）
├─ title, authors, journal, year
├─ abstract, introduction, methods, results, discussion  # IMRaD 分段
├─ full_text: str           # 全文（fallback）
├─ tables: list[dict]       # 提取的表格
├─ image_paths: list[Path]   # 提取的图片路径
└─ metadata: dict
```

### CheckResult (`base.py`)
统一的检测输出：

```
CheckResult
├─ check_id: str            # "numbers.statcheck"
├─ check_name: str          # "P 值反算 (py-statcheck)"
├─ level: "red" | "orange" | "yellow" | "green" | "error"
├─ verdict: str             # 人类可读的判定
├─ evidence: list[str]      # 证据链
├─ source_locations: list[SourceLocation]
├─ confidence: float        # 0-1
├─ needs_human: bool
└─ human_instruction: str   # 人工复核指引
```

### SourceLocation (`base.py`)
```
SourceLocation
├─ page: int
├─ figure_number: str | None
├─ paragraph: str | None
├─ excerpt: str | None
└─ screenshot_path: str | None
```

## 模块分层

```
paperfraud/
├─ base.py              # ParsedPaper, CheckResult, SourceLocation
├─ config.py            # Config dataclass
├─ cli.py               # Typer CLI, 调度全流程, _run_all_checks
│
├─ parser/
│  ├─ engine.py         # parse_pdf() 入口，分发 PyMuPDF/GROBID
│  └─ pymupdf_parser.py # PyMuPDF 解析 + IMRaD 分段
│
├─ checks/
│  ├─ numbers/
│  │  ├─ py_statcheck.py   # P 值反算 (t/F/χ²/r/Z → scipy)
│  │  ├─ grim.py           # GRIM 检验
│  │  ├─ digit_pref.py     # 尾数偏好 + 小数位一致性
│  │  └─ arithmetic.py     # 组间算术关系检测
│  ├─ images/
│  │  └─ lut.py            # LUT 伪彩映射
│  ├─ text/
│  │  ├─ blacklist.py      # 黑名单词扫描
│  │  └─ pvalue_camouflage.py  # P 值伪装话术
│  ├─ logic/               # Phase 2: LLM 辅助逻辑检查
│  └─ bioinfo/             # Phase 2: 生信代码/数据检查
│
├─ report/
│  ├─ aggregator.py   # 汇总 CheckResult → 红绿灯统计
│  └─ formatter.py    # 输出格式化 (terminal/json/markdown)
│
├─ external/           # Phase 2: 外部交叉验证
├─ llm/                # Phase 2: LLM 调用封装
└─ fetcher/            # Phase 2: DOI → PDF 自动下载
```

## 检查管道

所有检查函数签名统一为 `(ParsedPaper) → list[CheckResult]`。CLI 中 `_run_all_checks` 负责：
1. 根据 paper 内容组装检查列表（有图片才跑 image checks）
2. `ThreadPoolExecutor(max_workers=8)` 并行执行
3. 异常隔离：单个检查崩溃不影响其他检查，生成 `level="error"` 的 CheckResult

## Config 系统

```python
@dataclass
class Config:
    # PDF 获取
    scidb_domain: str = ""
    unpaywall_email: str = ""
    # 解析器
    grobid_url: str = ""          # 非空 → 走 GROBID
    # LLM
    llm_provider: str = "noop"    # "noop" | "openai" | "ollama"
    # 执行
    timeout: int = 600
    no_external: bool = False
    skip_images: bool = True      # 默认跳过图片提取
    max_pages: int = 0            # 0 = 全部
```

## Phase 1 检查清单（15 条）

| # | 检查项 | 模块 | 状态 |
|---|---|---|---|
| 1 | P 值反算 (statcheck) | numbers/py_statcheck.py | ✅ |
| 2 | GRIM 检验 | numbers/grim.py | ✅ |
| 3 | 尾数偏好 | numbers/digit_pref.py | ✅ |
| 4 | 小数位一致性 | numbers/digit_pref.py | ✅ |
| 5 | 组间算术关系 | numbers/arithmetic.py | ✅ |
| 6 | 黑名单词扫描 | text/blacklist.py | ✅ |
| 7 | P 值伪装话术 | text/pvalue_camouflage.py | ✅ |
| 8 | LUT 伪彩映射 | images/lut.py | ✅ |
| 9 | Clone Detection | images/ | Phase 2 |
| 10 | ELA 误差水平分析 | images/ | Phase 2 |
| 11 | 跨图复用检测 | images/ | Phase 2 |
| 12 | 核心假说审查 | logic/ | Phase 2 (需 LLM) |
| 13 | Overclaim 识别 | logic/ | Phase 2 (需 LLM) |
| 14 | 因果断言检查 | logic/ | Phase 2 (需 LLM) |
| 15 | 本福特定律 | numbers/ | Phase 2 |

## 关键设计决策

### GROBID vs PyMuPDF：双轨

- 默认 PyMuPDF（零依赖，准确率 ~60%）
- `--grobid-url http://localhost:8070` 切换到 GROBID（准确率 ~85%，需 Docker）
- 引擎层 (`parser/engine.py`) 根据 config 分发，上游无感

### statcheck：Python 重写，不用 R subprocess

- 正则提取 APA 格式统计量 (t/F/χ²/r/Z)
- scipy.stats 分布函数反算 P 值
- 对比报告 P 值和反算 P 值，标记 decision_error 和 gross_mismatch
- 原因：避免用户装 R 依赖；中文路径/编码问题

### LLM 完全可选

- Step 1 的 3 条逻辑检查标注 `needs_llm=True`
- `llm_provider = "noop"` 时静默跳过
- 不传 `--llm-provider` 只跑 12 条机械检查

### 图片提取默认关闭

- `skip_images = True` 默认
- `--extract-images` 启用
- 原因：大 PDF 图片提取是主要性能瓶颈

## 工程防护（三个暗礁）

### 1. 表格解析陷阱

生科论文表格常含跨行/跨列表头（Merged Cells），PyMuPDF 原生表格识别会错位。

**防护：**
- `arithmetic.py` 的 `extract_table_columns` 对每个 `float(n)` 包裹 try-except
- `run_arithmetic_check` 对 `extract_table_columns` 整体 try-except，失败返回 `level="yellow"` + "表格解析失败，跳过检查"
- 不对 parse 失败抛 `level="error"`，避免级联崩溃

### 2. 并行执行与线程安全

`ThreadPoolExecutor(max_workers=8)` 下多个检查同时读取 `ParsedPaper`。Python GIL 意味着纯 CPU 任务（正则、scipy）不会真正并行，但多线程提供了异常隔离。

**防护：**
- `ParsedPaper` 解析完成后所有字段只读（Python 字符串不可变，`image_paths` 仅解析时追加）
- 检查函数禁止修改 `ParsedPaper` 字段（见 `base.py` 注释）
- 单个检查崩溃被 `_run_all_checks` 的 try-except 隔离，生成 `level="error"` 的 CheckResult

### 3. 图片临时目录

`--extract-images` 提取的图片如放任不管会污染工作目录。程序异常中断时更容易残留。

**防护：**
- 图片写入 `tempfile.mkdtemp(prefix="paperfraud_")` 而非 PDF 同级目录
- `atexit.register(shutil.rmtree)` 兜底进程退出时清理
- CLI 在 `_run_all_checks` 完成后主动 `shutil.rmtree` 清理
- 双重保障：正常退出主动删，异常退出 atexit 兜底
