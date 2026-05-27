# 贡献指南

## 架构概览

```
PDF/DOCX → parser/engine.py
  → ParsedPaper (base.py)
  → 18 项 check 模块 (checks/)
  → report/aggregator.py (风险评分)
  → report/formatter.py (终端/JSON/Markdown)
  → web/app.py (Streamlit UI)
```

核心数据模型：
- `ParsedPaper` — 解析后的论文，包含分段的文本、图片路径、表格等
- `CheckResult` — 单个检查的结果，含 level（red/orange/yellow/green/error）、verdict、evidence
- `Config` — 运行时配置

## 添加一个新的检测模块

### 1. 创建检查函数

在 `paperfraud/checks/` 下对应的子目录（numbers/stats/text/bioinfo/images）创建 Python 文件。

所有检查函数必须遵循统一签名：

```python
def run_xxx(paper: ParsedPaper, config: Config | None = None) -> list[CheckResult]:
    """检查说明。"""
```

**规范**：
- 返回 `list[CheckResult]`（始终返回列表，即使只有一条结果）
- `paper` 参数只读——检查函数不能修改 ParsedPaper（确保 ThreadPoolExecutor 线程安全）
- 异常安全——单个检查失败不能导致整个 pipeline 崩溃
- 如果无法执行（如缺少必要文本字段），返回 `level="error"`

### 2. 在 CLI 中注册

编辑 `paperfraud/cli.py` 的 `_run_all_checks()` 函数，在对应的列表中添加你的模块：

```python
from paperfraud.checks.numbers.my_new_check import run_my_new_check

checks.extend([
    # ... 现有检查
    ("numbers.my_new_check", run_my_new_check),
])
```

### 3. 添加权重

编辑 `paperfraud/report/aggregator.py`，在 `WEIGHTS` 字典中为你的 check_id 添加风险权重（0-100）：

```python
WEIGHTS = {
    # ... 现有权重
    "numbers.my_new_check": 30,  # 根据严重程度设定
}
```

### 4. 编写测试

在 `tests/` 下创建测试文件，命名格式 `test_<module_name>.py`。

最小测试覆盖：
- 空文本/无数据 → error
- 正常通过 → green
- 命中检测 → red/orange/yellow

### 5. 更新文档

在 `docs/examples.md` 中添加你的检查项说明。

## CheckResult 等级判定指南

| 等级 | 使用场景 |
|------|----------|
| **Red** | 数学不可能、数据捏造证据、≥2 处 p 值伪装、n<3 统计推断 |
| **Orange** | 可疑模式但理论上可能、系统性异常、需要领域知识确认 |
| **Yellow** | 轻微异常、数据质量问题、边界情况 |
| **Green** | 通过检测，未发现异常 |
| **Error** | 无法执行（缺少必要数据/文本字段） |

## 数据驱动配置

黑名单词和 P 值伪装话术通过 YAML 文件配置：

- `paperfraud/checks/text/blacklist.yaml` — 过度宣称/夸大/绝对化词汇
- `paperfraud/checks/text/camouflage.yaml` — P 值伪装话术正则

编辑 YAML 即可生效，无需改 Python 代码。YAML 文件缺失或损坏时会自动回退到硬编码默认值。

## 依赖原则

- 能不引入第三方就不引入
- 新依赖必须是纯 Python 且广泛使用
- 可选功能用 `[project.optional-dependencies]` 分组

## PR 检查清单

- [ ] 检查函数遵循 `run_xxx(paper, config) -> list[CheckResult]` 签名
- [ ] 测试覆盖：空输入、正常通过、命中检测
- [ ] 所有现有测试依然通过 (`pytest tests/ -v`)
- [ ] 已在 `_run_all_checks()` 注册
- [ ] 已在 `WEIGHTS` 添加权重
- [ ] 已更新 `docs/examples.md`
