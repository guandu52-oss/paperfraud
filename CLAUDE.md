# CLAUDE.md — PaperFraud Detect

面试演示级学术论文造假检测系统。设计理念：**多步骤 Agent 架构 × 科学常识规则引擎 × 人工审查节点**。

## 系统架构

```
PDF → 文本/图片提取 (PyMuPDF)
  → 实体/参数抽取 (结构化规则 + LLM)
  → 三层交叉校验引擎
      ├─ 规则引擎: 硬编码的学科常识 (样本量/p值/Western Blot)
      ├─ 统计分析: Benford/GRIM/p-curve/效应量
      └─ LLM 审查: DeepSeek/Anthropic/OpenAI 定性分析
  → 风险评分 → HTML 报告 + 终端输出
```

## 目录结构

```
paperfraud/
├── pyproject.toml          # 唯一构建配置 (pip install -e .)
├── .gitignore
├── CLAUDE.md
├── README.md
├── scripts/
│   └── batch_run.py        # 批量处理工具
├── tests/
│   ├── fixtures/           # 测试 PDF
│   │   ├── akkermansia.pdf
│   │   └── tveq.pdf
│   └── ...
├── output/                 # 运行时输出 (gitignored)
│   └── <paper_name>/
│       ├── report.json
│       ├── images/         # 提取的原因
│       ├── ela_output/     # ELA 热力图
│       ├── lut_output/     # LUT 伪彩图
│       └── clone_output/   # Clone 检测图
├── docs/
│   ├── architecture.md
│   └── agent-prompt.md
└── paperfraud/             # Python 包
    ├── base.py             # 核心数据模型: CheckResult, ParsedPaper, SourceLocation
    ├── cli.py              # Typer CLI 入口: check / review / serve
    ├── config.py           # Config dataclass
    ├── parser/
    │   ├── engine.py       # 解析调度
    │   └── pymupdf_parser.py  # PyMuPDF 实现
    ├── checks/
    │   ├── bioinfo/        # 生物信息学检查
    │   │   └── western_blot.py
    │   ├── images/         # 图像取证 (仅生成辅助图, 不自动判定)
    │   │   ├── clone_detect.py  # 克隆区域检测 (pHash)
    │   │   ├── ela.py           # Error Level Analysis
    │   │   └── lut.py           # LUT 伪彩映射
    │   ├── numbers/        # 数值校验 (6个模块)
    │   ├── stats/          # 统计方法校验 (4个模块)
    │   └── text/           # 文本模式检测 (3个模块)
    ├── report/
    │   ├── aggregator.py   # 结果聚合 → 风险等级
    │   ├── formatter.py    # HTML + 终端 双输出
    │   └── templates/
    ├── review/
    │   ├── llm_review.py   # LLM 定性审查 (DeepSeek/Anthropic/OpenAI)
    │   └── prompts.py      # 审查 prompt 模板
    └── web/
        ├── app.py          # Streamlit Web UI
        ├── image_utils.py
        ├── comparison_viewer/  # 人工审查工作台 (Canvas 双图对比)
        └── image_selector/     # 图像取证浏览器
```

## 核心技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| PDF 解析 | PyMuPDF (fitz) | 文本提取 + 图片导出 |
| 图像处理 | OpenCV + PIL | ELA/LUT/Clone 检测 |
| 科学计算 | NumPy + SciPy | 统计检验 (t分布/p值/效应量) |
| CLI | Typer + Rich | 命令行 + 彩色终端 |
| Web UI | Streamlit | 报告浏览 + 图像审查 |
| LLM | openai SDK + anthropic SDK | DeepSeek/Claude/GPT 定性审查 |
| 报告 | Jinja2 | HTML 模板渲染 |

依赖策略：**能不引入第三方就不引入**。现有依赖全部必需，零冗余。

## 代码规范

### Python
- **命名**: snake_case 文件/函数/变量, PascalCase 类, UPPER_CASE 常量
- **类型**: 所有公开函数必须带 type hints, 用 `from __future__ import annotations` 延迟求值
- **docstring**: 仅公开函数需要一行描述。无需 Google/NumPy 风格长文档
- **导入顺序**: `__future__` → stdlib → 第三方 → 内部 (`paperfraud.xxx`)
- **字符串**: 中文提示/报告用全角标点，英文日志用 ASCII
- **dataclass**: 所有数据结构用 `@dataclass`, 字段默认值避免可变对象

### 检查模块规范
```python
def run_xxx(paper: ParsedPaper, config: Config | None = None) -> list[CheckResult]:
    """每个检查模块的入口函数签名必须统一。"""
```
- 输入: `ParsedPaper` + 可选 `Config`
- 输出: `list[CheckResult]` (永远返回列表，即使只有一个结果)
- `CheckResult.level`: red > orange > yellow > green > error
- `CheckResult.needs_human`: True 表示需要人工复核

### 关键设计决策 (不要推翻)
1. **图片检测不自动判定** — LUT/ELA/Clone 只生成辅助图，level 固定 green，靠人眼看
2. **图像特征匹配 (ORB) 已移除** — 假阳性太高，跨图重复检测不可靠
3. **文字遮罩已移除** — LUT/ELA 保留完整图片含文字，人工审查需要看完整内容
4. **`--clean-images` 已移除** — 功能无意义
5. **样本量处理三档**: n<3 无法统计, n=3 加警告, n>3 完整分析
6. **决不全局求 min(n) 绑定错误上下文** — 每个可疑声明独立分析

## 常用命令

```bash
# 安装 (开发模式)
cd paperfraud && pip install -e ".[dev,web]"

# 单篇检查 (无图片)
PYTHONPATH="." python3 -m paperfraud.cli check "tests/fixtures/akkermansia.pdf"

# 单篇检查 (含图片取证)
PYTHONPATH="." python3 -m paperfraud.cli check "tests/fixtures/tveq.pdf" \
  --extract-images --output-dir output/demo

# 交互式审查 (LLM 二次确认)
PYTHONPATH="." python3 -m paperfraud.cli review output/demo/report.json

# 启动 Web UI
PYTHONPATH="." streamlit run paperfraud/web/app.py --server.port 8502 \
  -- --report output/demo/report.json

# 批量处理
PYTHONPATH="." python3 scripts/batch_run.py "tests/fixtures/" --extract-images

# 运行测试
pytest tests/ -v
```

## 环境要求

- Python >= 3.9
- macOS/Linux (Windows 未测试)
- LLM 审查需要设置环境变量: `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, 或 `OPENAI_API_KEY`

## 面试演示要点

向审查者展示时强调:
1. **三层交叉校验引擎** — 不是 LLM 一把梭，是用规则 + 统计 + AI 的分层架构
2. **科学常识规则是核心竞争力** — 比如 "n=3 跑不出 p<0.001"、"Western blot 必须有 loading control"、"p值全在 0.04 附近是 p-hacking"
3. **图像部分诚实处理** — 评估后决定不自动判定，保留人工审查节点。这在 medical-grade 准确率要求下是正确的工程取舍
4. **全链路可运行** — 一个命令跑通 PDF → 18 项检查 → HTML 报告
