# PaperFraud 准确度优化 — LLM 审查层 + 综合判定逻辑

日期：2026-05-27
范围：`paperfraud/review/prompts.py`, `paperfraud/review/llm_review.py`, `paperfraud/report/aggregator.py`
原则：最小改动、最大效果。不改检测规则本身（14 条规则的阈值/逻辑不动），不改解析层。

---

## 一、LLM 审查层优化

### 1.1 System Prompt 重写（prompts.py）

**删除：**
- "图像检测信号（clone/LUT/ELA）权重最高，置信度原则上不可推翻"
  — 与设计文档矛盾。图像检测不自动判定，保留人工审查节点。

**新增三段领域知识：**

```
### 实验类型判断指南
- 细胞/分子实验 (in vitro)：n=3 是常见设计，但需结合 loading control 和重复次数判断
- 动物实验 (in vivo)：n=3-5 功效极低，p<0.01 需审查原始数据
- 临床/人体研究：小样本不具有统计意义，区分探索性 vs 验证性研究

### 技术重复 vs 生物学重复
- "in triplicate"、"3 wells per sample"、"technical replicates" → 技术重复，非生物学 n
- 若论文将技术重复当作生物学重复跑统计检验（伪重复/pseudoreplication）→ true_positive

### 跨信号关联
- ≥2 个信号指向同一段落同一组数据 → 互相印证，可信度显著提升
- 孤立信号来自 PDF 文本提取噪声（行内换行导致 p 值被分割）→ 倾向于假阳性
```

### 1.2 Methods/Results 精准投喂（prompts.py: build_review_prompt）

**现状：** 只传 `paper.abstract`，截断 2500 字符。LLM 看不到 Methods，无法判断 n=3 在细胞实验 vs 动物实验中的合理性。

**改为：**

```python
KEYWORDS = ["n=", "n =", "p <", "p =", "p>", "mice", "cells", "patients",
            "triplicate", "SD", "SEM", "±", "western", "loading",
            "biological", "independent", "experiment", "replicate"]

def _extract_evidence_paragraphs(text: str, keywords: list[str], max_chars: int = 3000) -> str:
    """从 Methods/Results 全文提取包含关键词的段落，精准投喂而非盲目截断。"""
    paragraphs = text.split("\n\n")
    hits = []
    for para in paragraphs:
        if any(kw.lower() in para.lower() for kw in keywords):
            hits.append(para)
    result = ""
    for h in hits:
        if len(result) + len(h) > max_chars:
            remaining = max_chars - len(result)
            if remaining > 200:
                result += h[:remaining] + "..."
            break
        result += h + "\n\n"
    return result.strip()
```

**build_review_prompt 改动：**
- 移除 `_smart_truncate(paper.abstract, 2500)` 调用
- 新增：提取 `paper.methods` 的关键段落 → 标记为 `[Methods 关键段落]`
- 新增：提取 `paper.results` 的关键段落 → 标记为 `[Results 关键段落]`
- 保留 `paper.abstract` 但不作为主要证据来源

### 1.3 Few-shot 校准（prompts.py: SYSTEM_PROMPT 末尾追加）

**案例 1 — 正样本（造假论文）：**
```
## 审查案例 1（造假论文）
论文：n=3/group 动物实验，声称 p<0.001
检测信号：
  - stats.sample_size: RED — n=3 声称 p<0.001，需 d≥3.5，生物实验中几乎不可能
  - numbers.grim: RED — M=4.37, N=12 → N×M=52.44，非整数
  - stats.p_hacking: RED — 8/11 个精确 p 值在 0.04-0.05

正确审查输出：
  overall_assessment: "多维度信号交叉印证：小样本+极端声称+GRIM数学不一致+p-curve异常。信号来自Methods和Results不同段落，非孤立噪声。"
  severity_score: 9
  signal_reviews: 全部 is_true_positive=true, severity=high
  pubpeer_draft: "该文报告的 n=3/group 动物实验声称 p<0.001，达到此显著性需 d≥3.5，远超生物实验合理范围（d=0.8-2.0）。同时 GRIM 检验显示 M=4.37 和 N=12 数学上不兼容，p-curve 呈现经典 p-hacking 模式。"
```

**案例 2 — 负样本（假阳性消除）：**
```
## 审查案例 2（假阳性 — 不造假）
论文：细胞实验，Western blot 检测
检测信号：
  - stats.sample_size: YELLOW — 全文检测到 n=3，但 Methods 声明 "Western blot was performed in triplicate"
  - text.blacklist: YELLOW — "significantly increased" 出现 5 次

正确审查输出：
  overall_assessment: "仅有的黄色信号均可解释为假阳性。'n=3'实为技术重复（in triplicate），非生物学样本量。'significantly increased'是生命科学标准用语，非黑名单话术。"
  severity_score: 1
  signal_reviews:
    - check_id: "stats.sample_size" → is_true_positive=false, severity=false_alarm
      理由："in triplicate 为技术重复，非生物学 n，不构成样本量不足"
    - check_id: "text.blacklist" → is_true_positive=false, severity=false_alarm
      理由："significantly increased 为标准科学用语"
  pubpeer_draft: ""
```

### 1.4 参数调整（llm_review.py）

| 参数 | 旧值 | 新值 | 理由 |
|------|------|------|------|
| max_tokens | 4096 | 8192 | 多信号论文不被截断；DeepSeek/Anthropic 均支持 |
| temperature | 0.1 | 0.05 | 提高一致性，0.1 在某些 provider 下输出不稳定 |

---

## 二、综合判定逻辑重构

### 2.1 风险评分引擎（aggregator.py 重写）

**替代** "有 1 个红 → 整体红" 的简单叠加。

```
RiskScore = min(Σ(信号分 × 检测权重 × 相关性系数) + 聚类加分, 100)
```

### 2.2 检测权重表

| check_id | 权重 | 理由 |
|----------|------|------|
| numbers.grim | 1.0 | 数学确定性——M×N 非整数 = 数据不可能来自整数测量 |
| numbers.statcheck | 0.9 | scipy 精确反算；假阳仅来自四舍五入 |
| stats.sample_size | 0.85 | 基于 t 分布和效应量公式，数学前提 |
| numbers.arithmetic | 0.8 | 组间均值/百分比加减不一致，数学硬伤 |
| numbers.identical_values | 0.7 | 多组均值/SD 完全一致极不可能 |
| bioinfo.western_blot | 0.6 | loading control 是学科常识；有不写明的情况 |
| stats.p_hacking | 0.5 | p-curve 依赖提取完整性；PDF 文本提取天然噪声 |
| stats.normality_claim | 0.4 | n=5 时正态性不可验证；很多作者无意识写了 |
| numbers.benford | 0.4 | 统计概率非数学证明；天然高假阳性 |
| stats.fallacies | 0.4 | 因果倒置等语言模式；需上下文判断 |
| text.pvalue_camouflage | 0.3 | 话术匹配；可能被正常语境触发 |
| text.blacklist | 0.3 | 黑名单词扫描；正常科学写作可能命中 |
| numbers.digit | 0.3 | 小数位模式；需大量数据点才可靠 |
| text.title_conclusion_gap | 0.3 | 标题-结论 gap；主观性较强 |
| images.* | 不在评分体系中 | 只生成辅助图，不产生 red/orange 信号 |

### 2.3 信号分

| level | 分数 |
|-------|------|
| red | 25 |
| orange | 12 |
| yellow | 5 |
| green/error | 0 |

### 2.4 信号聚类（Jaccard 相似度 + 页邻近度）

**替代** excerpt 精确字符串匹配。解决 PDF 跨页断段、连字符截断导致的匹配失败。

```python
import re

STOPWORDS = {"the", "a", "an", "of", "in", "to", "and", "or", "for", "with",
             "was", "were", "is", "are", "be", "by", "as", "at", "on", "from",
             "that", "this", "it", "not", "but", "we", "our", "their", "its",
             "has", "have", "had", "can", "may", "will", "would", "could",
             "also", "used", "using", "each", "all", "between"}

def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r'\b[\w.]+\b', text.lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
```

**相关性判定规则：**

| 条件 | 相关性系数 |
|------|-----------|
| 同一页 + Jaccard ≥ 0.3 | 1.5 |
| 相邻页（差 1）+ Jaccard ≥ 0.3 | 1.5（跨页断段） |
| 同一页 + Jaccard ≥ 0.15（＜0.3） | 1.2（弱同源） |
| 上述条件均不满足 | 1.0（孤立信号） |

### 2.5 聚类加分

```
≥3 个信号（相关性系数 > 1.0）指向同一聚类 → +10
≥5 个信号（相关性系数 > 1.0）指向同一聚类 → +20
```

### 2.6 一票否决权（Veto Override）

```python
VETO_CHECKS = {"numbers.grim", "numbers.arithmetic"}

def _veto_override(results: list[CheckResult]) -> dict | None:
    """数学铁证不能被子分稀释。"""
    for r in results:
        if r.check_id in VETO_CHECKS and r.level == "red" and r.confidence >= 0.9:
            return {
                "overall_level": "red",
                "overall_verdict": (
                    f"检测到数学铁证（{r.check_name}）："
                    f"基础数据在数学上不可能。建议立即审查原始数据。"
                ),
                "veto_trigger": r.check_id,
            }
    return None
```

触发条件：GRIM 或 arithmetic 命中 red **且** confidence ≥ 0.9。
Veto 跳过所有后续计算，直接输出 red。

### 2.7 Floor 保底 + Cap 截断

```python
LEVEL_ORDER = {"red": 4, "orange": 3, "yellow": 2, "green": 1, "error": 0}

# Cap
risk_score = min(risk_score, 100)

# Floor：致命红信号不能被低权重稀释
max_single = max(results, key=lambda r: LEVEL_ORDER.get(r.level, 0))
if max_single.level == "red":
    risk_score = max(risk_score, 51)
elif max_single.level == "orange":
    risk_score = max(risk_score, 31)
```

### 2.8 输出映射

```
RiskScore 0-15  → green  "未发现系统性造假信号"
RiskScore 16-30 → yellow "存在孤立疑点，可能为检测噪声或文本提取误差"
RiskScore 31-50 → orange "多处可疑信号，建议人工逐条复核"
RiskScore 51-100 → red   "高度可疑——多维度信号交叉印证，建议深入调查"
```

**注意：** Floor 保底确保 "有一个红 → 至少进 red"，加权算法不影响存在性。

### 2.9 人类复核优先级排序

`aggregated["needs_human"]` 当前是无序列表。改为按贡献度降序：

```python
def _rank_human_review(results: list[CheckResult], weights: dict, correlations: dict) -> list[dict]:
    """按 RiskScore 贡献度排序 needs_human 列表。"""
    scored = []
    for r in results:
        if not r.needs_human:
            continue
        signal_score = 25 if r.level == "red" else 12 if r.level == "orange" else 5
        contribution = signal_score * weights.get(r.check_id, 0.5) * correlations.get(r.check_id, 1.0)
        scored.append((contribution, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r.to_dict() for _, r in scored]
```

### 2.10 aggregate_results 新签名

```python
def aggregate_results(results: list[CheckResult]) -> dict:
    """返回包含 risk_score 和详细分解的新结构。"""
    return {
        "red_count": ...,
        "orange_count": ...,
        "yellow_count": ...,
        "green_count": ...,
        "error_count": ...,
        "total_checks": ...,
        "overall_level": ...,
        "overall_verdict": ...,
        "risk_score": risk_score,        # 新增：0-100
        "risk_breakdown": {              # 新增：评分分解（终端/Web 展示用）
            "total": risk_score,
            "contributions": [...],      # 每个信号的贡献分
            "cluster_bonus": ...,
            "veto_triggered": ...,
            "floor_applied": ...,
        },
        "needs_human": [...],            # 已按贡献度降序
        "needs_human_count": ...,
    }
```

---

## 三、不改的范围（明确边界）

- **14 条检测规则**：阈值、判定逻辑不动。这是独立的优化维度
- **解析层**：`parser/`、`base.py`、`config.py` 不动
- **报告层**：`formatter.py` 除了适配 `aggregated` 新字段外不动
- **Web UI**：Streamlit 适配新 aggregated 结构，功能不变
- **图像管线**：不动

## 四、涉及文件

| 文件 | 改动类型 | 改动量 |
|------|---------|--------|
| `paperfraud/review/prompts.py` | 重写 SYSTEM_PROMPT、改写 build_review_prompt、新増 _extract_evidence_paragraphs | ~80 行 |
| `paperfraud/review/llm_review.py` | 改 max_tokens 和 temperature | 2 行 |
| `paperfraud/report/aggregator.py` | 完全重写 | ~150 行 |
| `paperfraud/cli.py` | `_print_terminal_report` 适配新 aggregated 字段 | ~15 行 |
| `paperfraud/report/formatter.py` | JSON/Markdown 格式适配新字段 | ~20 行 |

## 五、验证标准

1. **回归**：`akkermansia.pdf` 和 `tveq.pdf` 跑 check 不报错，输出格式兼容
2. **Veto**：手动构造 GRIM red + confidence=0.95 的 CheckResult，aggregate 直接输出 red 且 risk_score ≥ 51
3. **Floor**：构造 1 red + 9 green，risk_score ≥ 51
4. **聚类**：构造 3 个同 page 同 excerpt 的 red，相关性系数 1.5 + 聚类加分 +10 生效
5. **LLM**：用 `--review` 跑 tveq.pdf，LLM 返回包含 signal_reviews 且 is_true_positive 判定合理
6. **Few-shot 不泄漏**：Few-shot 案例使用虚构数据，不与真实测试 PDF 相似
