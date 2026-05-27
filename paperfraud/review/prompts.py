"""Prompt templates for LLM qualitative review.

Key improvements over v1:
  - Methods/Results keyword-targeted evidence injection (not blind abstract truncation)
  - Experiment-type-aware judgment guides (cell vs animal vs clinical)
  - Two few-shot calibration examples (one positive, one false-positive)
  - Fixed contradiction: image signals are NOT auto-determinative
"""
from __future__ import annotations

from paperfraud.base import CheckResult, ParsedPaper

# ── Keywords for evidence paragraph extraction ──────────────────────────────
EVIDENCE_KEYWORDS = [
    "n=", "n =", "p<", "p <", "p=", "p =", "p>", "p >",
    "mice", "cells", "patients",
    "triplicate", "SD", "SEM",
    "western", "loading", "biological",
    "independent", "experiment", "replicate",
]

SYSTEM_PROMPT = """你是一位学术论文造假审查专家。你会收到一份自动化检测报告，包含：
1. 论文元数据（标题、期刊、Methods/Results 关键段落）
2. 每个 Red/Orange/Yellow 信号的 evidence 原文上下文（"案发现场"）
3. 所有自动化检测的完整结果

你的任务：
- 逐条审查每个 Red/Orange/Yellow 信号，判断是"真锤"（确实可疑）还是"假阳性"（检测噪声/正常模式）
- 跨信号关联分析：如果多项信号指向同一底层问题（如"统计素养系统性缺陷"），请明确指出
- 撰写一段可直接发表在 PubPeer 上的中文审稿意见（必须包含具体证据引用，不能泛泛而谈）

### 实验类型判断指南
- 细胞/分子实验 (in vitro)：n=3 是常见设计，但需结合 loading control 和重复次数判断
- 动物实验 (in vivo)：n=3-5 功效极低，p<0.01 需审查原始数据
- 临床/人体研究：小样本不具有统计意义，需区分探索性 vs 验证性研究

### 技术重复 vs 生物学重复
- "in triplicate"、"3 wells per sample"、"technical replicates" → 技术重复，不是生物学 n
- 若论文将技术重复当作生物学重复来跑统计检验（伪重复/pseudoreplication），标记为 true_positive
- 若原文声明了技术重复但在其他段落使用了足够的生物学重复，标记为 false_alarm

### 跨信号关联
- ≥2 个信号指向同一段落同一组数据 → 互相印证，可信度显著提升
- 孤立信号来自 PDF 文本提取噪声（行内换行导致 p 值被分割、连字符截断等）→ 倾向于假阳性
- 多个信号分布在 Methods/Results/图注的不同位置但指向同一统计问题 → 系统性缺陷，severity 升级

### 判断标准
- confidence < 0.5 的信号：倾向于假阳性，除非 evidence 原文非常明确
- PDF 文本提取来源的信号：天然噪声大，需谨慎判定
- 统计/文本信号若指向同一问题 → 互相印证，可信度提升
- 论文 Methods 中声明的实验设计信息应作为主要判断依据

输出要求：严格 JSON 格式，无额外文字、无 markdown 标记。

---

## 审查案例 1（造假论文 — 正样本校准）

论文：动物实验，n=3/group，声称 p<0.001
Methods 关键信息：C57BL/6 mice, n=3 per group, Student's t-test, p<0.05 considered significant

检测信号：
  - stats.sample_size: RED — n=3 声称 p<0.001，需 d≥3.5，动物实验中几乎不可能
  - numbers.grim: RED — M=4.37, N=12 → N×M=52.44，非整数，GRIM 不一致
  - stats.p_hacking: RED — 8/11 个精确 p 值在 0.04-0.05 区间
  - text.blacklist: YELLOW — "significantly increased" 出现 8 次

正确审查输出：
```json
{
  "overall_assessment": "多维度信号交叉印证：小样本+极端声称+GRIM数学不一致+p-curve异常。信号分布在Methods和Results不同段落，非孤立噪声。动物实验n=3声称p<0.001在生物学上极不合理。",
  "severity_score": 9,
  "signal_reviews": [
    {
      "check_id": "stats.sample_size",
      "is_true_positive": true,
      "reasoning": "动物实验n=3声称p<0.001需效应量d≥3.5，远超动物实验典型范围(d=0.8-2.0)。Methods确认n=3，非文本提取误差。",
      "severity": "high"
    },
    {
      "check_id": "numbers.grim",
      "is_true_positive": true,
      "reasoning": "M=4.37 × N=12 = 52.44，非整数，数学上不可能来自整数测量值。GRIM为数学确定性检验，假阳性率极低。",
      "severity": "high"
    },
    {
      "check_id": "stats.p_hacking",
      "is_true_positive": true,
      "reasoning": "8/11个p值集中在0.04-0.05，为经典p-hacking模式。与样本量不足形成逻辑闭环：小样本无法达到显著→反复调整分析→p值聚集在阈值附近。",
      "severity": "high"
    },
    {
      "check_id": "text.blacklist",
      "is_true_positive": false,
      "reasoning": "significantly increased是生命科学标准统计用语，非造假话术。8次出现在合理范围内。",
      "severity": "false_alarm"
    }
  ],
  "pubpeer_draft": "该文报告n=3/group C57BL/6小鼠实验并声称p<0.001。达到此显著性需Cohen's d≥3.5，远超动物实验合理效应量范围(d=0.8-2.0)。同时GRIM检验显示报告均值M=4.37与样本量N=12数学上不兼容(N×M=52.44非整数)。p-curve分析显示8/11个精确p值集中于0.04-0.05区间，呈经典p-hacking模式。三项独立信号从样本量合理性、基础算术一致性、p值分布三个维度交叉印证，强烈提示数据不可靠。建议作者提供原始数据。"
}
```

---

## 审查案例 2（假阳性 — 负样本校准）

论文：细胞实验，Western blot + qPCR
Methods 关键信息：HEK293T cells, Western blot was performed in triplicate, qPCR with three biological replicates

检测信号：
  - stats.sample_size: YELLOW — 全文检测到 n=3
  - text.blacklist: YELLOW — "dramatically increased" 出现 2 次

正确审查输出：
```json
{
  "overall_assessment": "仅有的黄色信号均可解释为假阳性。'n=3'实为Methods中声明的技术重复(in triplicate)，且qPCR使用三个生物学重复。'dramatically increased'为偶发修辞，非系统性话术模式。论文整体方法学描述完整。",
  "severity_score": 1,
  "signal_reviews": [
    {
      "check_id": "stats.sample_size",
      "is_true_positive": false,
      "reasoning": "Methods明确声明Western blot was performed in triplicate——这是技术重复，不是生物学n=3。且qPCR部分使用了three biological replicates。自动化检测无法区分技术vs生物学重复，需人工判定为假阳性。",
      "severity": "false_alarm"
    },
    {
      "check_id": "text.blacklist",
      "is_true_positive": false,
      "reasoning": "dramatically increased出现仅2次，属正常科学写作中的偶发修辞，非黑名单话术的系统性使用。",
      "severity": "false_alarm"
    }
  ],
  "pubpeer_draft": ""
}
```"""


def _extract_evidence_paragraphs(
    text: str, keywords: list[str], max_chars: int = 3000
) -> str:
    """Extract paragraphs containing statistical/methodological keywords.

    Avoids blind text truncation — only feeds the LLM "meat" (paragraphs
    that actually contain n=, p<, SD, SEM, western, triplicate, etc.).
    """
    if not text:
        return ""

    paragraphs = text.split("\n\n")
    hits = []
    for para in paragraphs:
        para_lower = para.lower()
        if any(kw.lower() in para_lower for kw in keywords):
            hits.append(para.strip())

    if not hits:
        return ""

    result = ""
    for h in hits:
        if len(result) + len(h) + 2 > max_chars:
            remaining = max_chars - len(result)
            if remaining > 200:
                result += h[:remaining] + "..."
            break
        result += h + "\n\n"

    return result.strip()


def build_review_prompt(
    paper: ParsedPaper, aggregated: dict, results: list[CheckResult]
) -> str:
    """Build the user prompt for LLM review.

    Key changes from v1:
      - Methods/Results keyword-targeted paragraphs replace blind abstract truncation
      - Evidence injection carries section labels for context
    """
    sections = []

    # ── Paper metadata ──────────────────────────────────────────────────
    sections.append("# 论文信息")
    sections.append(f"标题：{paper.title or '未知'}")
    if paper.journal:
        sections.append(f"期刊：{paper.journal}")
    if paper.year:
        sections.append(f"年份：{paper.year}")
    if paper.authors:
        authors_str = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors_str += f" 等 ({len(paper.authors)} 人)"
        sections.append(f"作者：{authors_str}")
    sections.append("")

    # ── Methods/Results key paragraphs (the "meat") ─────────────────────
    if paper.methods:
        methods_evidence = _extract_evidence_paragraphs(
            paper.methods, EVIDENCE_KEYWORDS, max_chars=3000
        )
        if methods_evidence:
            sections.append("# Methods 关键段落（实验设计信息）")
            sections.append(methods_evidence)
            sections.append("")

    if paper.results:
        results_evidence = _extract_evidence_paragraphs(
            paper.results, EVIDENCE_KEYWORDS, max_chars=3000
        )
        if results_evidence:
            sections.append("# Results 关键段落（统计报告信息）")
            sections.append(results_evidence)
            sections.append("")

    # ── Abstract (kept for overview, lower priority) ────────────────────
    if paper.abstract:
        abstract = paper.abstract[:1500]
        sections.append("# 摘要（仅作概览参考）")
        sections.append(abstract)
        sections.append("")

    # ── Signal review ───────────────────────────────────────────────────
    level_labels = {
        "red": "红色信号（严重）",
        "orange": "橙色信号（可疑）",
        "yellow": "黄色信号（需关注）",
    }

    for level in ["red", "orange", "yellow"]:
        level_results = [r for r in results if r.level == level]
        if not level_results:
            continue

        sections.append(f"# {level_labels[level]}")
        sections.append("")

        for i, r in enumerate(level_results, 1):
            sections.append(f"## {i}. [{r.check_id}] {r.check_name}")
            sections.append(f"判定：{r.verdict}")
            sections.append(f"置信度：{r.confidence:.0%}")
            if r.evidence:
                sections.append("证据原文（案发现场）：")
                for e in r.evidence[:8]:
                    sections.append(f"  - {e}")
            if r.human_instruction:
                sections.append(f"人工复核指引：{r.human_instruction}")
            sections.append("")

    # ── Green / Error summary ───────────────────────────────────────────
    green_results = [r for r in results if r.level == "green"]
    if green_results:
        sections.append("# 绿色信号（已通过）")
        sections.append(f"以下 {len(green_results)} 项检测未发现异常：")
        for r in green_results:
            sections.append(f"  - {r.check_name}")
        sections.append("")

    error_results = [r for r in results if r.level == "error"]
    if error_results:
        sections.append("# 无法执行的检测")
        for r in error_results:
            sections.append(f"  - {r.check_name}: {r.verdict}")
        sections.append("")

    # ── JSON output instruction ─────────────────────────────────────────
    sections.append("---")
    sections.append(
        "请基于以上信息，输出以下 **严格的 JSON 格式**"
        "（字段名必须完全一致，不得修改）："
    )
    sections.append("")
    sections.append("```json")
    sections.append("{")
    sections.append(
        '  "overall_assessment": '
        '"综合判断中文文本，1-2段，总结关键发现和整体可信度评估",'
    )
    sections.append('  "severity_score": 7,')
    sections.append('  "signal_reviews": [')
    sections.append("    {")
    sections.append('      "check_id": "check.id",')
    sections.append('      "is_true_positive": true,')
    sections.append('      "reasoning": "判定理由",')
    sections.append('      "severity": "high"')
    sections.append("    }")
    sections.append("  ],")
    sections.append(
        '  "pubpeer_draft": '
        '"可直接发表在 PubPeer 上的中文审稿意见草稿，需引用具体证据"'
    )
    sections.append("}")
    sections.append("```")
    sections.append("")
    sections.append("字段说明：")
    sections.append("- overall_assessment: 综合中文判断（必填）")
    sections.append("- severity_score: 0-10 造假嫌疑评分（必填）")
    sections.append(
        "  0-2: 无造假信号  3-4: 有疑点  5-6: 值得关注  "
        "7-8: 高度怀疑  9-10: 铁证"
    )
    sections.append(
        "- signal_reviews: 每个 Red/Orange/Yellow 信号的审查"
        "（必填，至少包含所有非绿色信号）"
    )
    sections.append("  - check_id: 检测项 ID（必填）")
    sections.append("  - is_true_positive: true=真锤, false=假阳性（必填）")
    sections.append("  - reasoning: 判定理由（必填）")
    sections.append(
        "  - severity: 严重程度 high/medium/low/false_alarm（必填）"
    )
    sections.append(
        "- pubpeer_draft: 中文审稿草稿，可公开发表，需引用具体证据（必填）"
    )

    return "\n".join(sections)


# ── Interactive Chat Prompt ───────────────────────────────────────────────
# Used by the Web UI "💬 交互问答" page. The full report JSON is injected
# as a system message alongside this prompt, so the model can reference
# specific signals and evidence in its answers.

INTERACTIVE_CHAT_PROMPT = """你是一个学术论文造假检测助手。你会收到一份完整的检测报告（JSON 格式），用户将针对报告内容提问。

报告包含：
- summary: 综合风险评分、各级别信号数量、总体判定
- risk_breakdown: 每个信号的贡献分、权重、相关性
- checks: 每个检测项的详细结果——级别(red/orange/yellow/green)、判定、证据、置信度

回答规则：
1. 始终引用报告中的具体数据和证据，不要凭空猜测
2. 如果某个信号置信度 < 0.8，请说明
3. 如果用户问"最弱"或"最可能是假阳性"的信号，结合置信度和证据链判断
4. 回答简洁，2-4 句话即可，不要重复报告全文
5. 用中文回答

报告如下："""
