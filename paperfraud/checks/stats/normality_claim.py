"""Normality assumption claim checker.

In frequentist statistics, the normality assumption applies to MODEL RESIDUALS,
not raw data. Many papers incorrectly state "data were normally distributed" or
"all data met the assumption of normal distribution."

This is a well-known statistical error:
- Raw data CANNOT be normally distributed across groups (would be multi-modal
  if there are treatment effects — which is the whole point of the experiment)
- The correct statement is: "residuals met the normality assumption" or
  "the residuals were approximately normally distributed"
- "All data met the assumption of normal distribution" is statistically
  nonsensical when comparing groups

Flagging this is NOT nitpicking — it often indicates either:
  1. Superficial understanding of statistics (red flag for overall rigor)
  2. Boilerplate text inserted without actual testing (paper-mill pattern)
"""

from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation


NORMALITY_CLAIM_PATTERNS = [
    (
        re.compile(
            r'(?:all\s+)?(?:the\s+)?data\s+(?:met|satisfied|followed|passed)\s+'
            r'(?:the\s+)?(?:assumption\s+of\s+)?(?:a\s+)?normal\s+(?:distribution|distributions)',
            re.IGNORECASE,
        ),
        "red",
        "声称'数据'符合正态分布——统计错误，应为模型残差符合正态分布。"
        "在不同处理组间存在效应的情况下，原始数据不可能符合单一正态分布。",
    ),
    (
        re.compile(
            r'(?:all\s+)?data\s+(?:were|are|was)\s+(?:normally\s+distributed|found\s+to\s+be\s+normal)',
            re.IGNORECASE,
        ),
        "red",
        "声称'数据呈正态分布'——经典统计学错误。应报告残差的正态性检验结果。",
    ),
    (
        re.compile(
            r'(?:Shapiro[.-]Wilk|Kolmogorov[.-]Smirnov|D\'?Agostino[.-]Pearson)\s+'
            r'(?:test|normality\s+test)\s+(?:showed|indicated|confirmed|revealed)\s+'
            r'(?:that\s+)?(?:the\s+)?data',
            re.IGNORECASE,
        ),
        "yellow",
        "使用正态性检验但检验对象为'数据'而非'残差'。如为单组描述性统计可接受，"
        "多组比较时检验原始数据无意义。",
    ),
    (
        re.compile(
            r'(?:a\s+)?normality\s+(?:test|check)\s+(?:was\s+)?(?:used|performed|employed|conducted)',
            re.IGNORECASE,
        ),
        "yellow",
        "提及正态性检验。需核实检验对象是否为模型残差。",
    ),
]


def run_normality_claim(paper) -> list[CheckResult]:
    """Check for incorrect normality claim statements in Methods."""
    text = paper.methods or paper.full_text
    if not text:
        return [
            CheckResult(
                check_id="stats.normality_claim",
                check_name="正态性声明审计",
                level="error",
                verdict="无法执行：未提取到 Methods 文本",
                needs_human=False,
            )
        ]

    all_hits = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern, severity, explanation in NORMALITY_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 80)
            context = text[start:end].replace('\n', ' ').strip()
            all_hits.append({
                "matched": match.group(0),
                "severity": severity,
                "explanation": explanation,
                "context": context,
            })

    evidence = []
    for h in all_hits:
        evidence.append(f"[{h['severity'].upper()}] \"{h['matched']}\"")
        evidence.append(f"  → {h['explanation']}")
        evidence.append(f"  上下文: ...{h['context']}...")

    red_hits = [h for h in all_hits if h["severity"] == "red"]
    yellow_hits = [h for h in all_hits if h["severity"] == "yellow"]

    if red_hits:
        level = "red"
        verdict = (
            f"严重统计错误：Methods 中 {len(red_hits)} 处声称'数据符合正态分布'，"
            f"应为模型残差。这在不同处理组间存在效应时在统计学上是不成立的。"
        )
    elif yellow_hits:
        level = "yellow"
        verdict = (
            f"Methods 中 {len(yellow_hits)} 处提及正态性检验，需核实检验对象是否正确（残差 vs 原始数据）"
        )
    else:
        level = "green"
        verdict = "未发现'数据符合正态分布'的统计学错误表述"

    return [
        CheckResult(
            check_id="stats.normality_claim",
            check_name="正态性声明审计",
            level=level,
            verdict=verdict,
            evidence=evidence[:15],
            source_locations=[
                SourceLocation(page=1, excerpt=h["context"]) for h in all_hits[:3]
            ],
            confidence=0.9,
            needs_human=len(all_hits) > 0,
            human_instruction="核实 Methods 中正态性检验的具体对象。"
            "若为单组描述性统计（如基线特征），检验原始数据可接受。"
            "若为多组比较（如 ANOVA/t-test），必须检验模型残差而非原始数据。"
            "这是一个衡量论文统计严谨性的重要指标。",
        )
    ]
