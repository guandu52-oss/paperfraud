"""P-value linguistic camouflage detection.

Scans Discussion/Results for phrases that disguise non-significant
results as significant:

  "trending towards significance"      — P > 0.05
  "marginally significant"             — P > 0.05
  "almost significant"                 — P > 0.05
  "approached significance"            — P > 0.05
  "borderline significant"             — P > 0.05
  "trend towards significance"         — P > 0.05
  "nominally significant"              — before correction

In statistics, there is no "almost pregnant." If P > 0.05,
results must be reported as non-significant.
"""

from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation


CAMOUFLAGE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r'(?:trending|tend(?:ed|ing)?)\s+towards?\s+(?:statistical\s+)?significance',
            re.IGNORECASE,
        ),
        "trending towards significance — P > 0.05，暗示接近显著但实际不显著",
    ),
    (
        re.compile(
            r'marginally\s+significant',
            re.IGNORECASE,
        ),
        "marginally significant — 经典 P 值伪装话术，P 值通常在 0.05-0.10 之间",
    ),
    (
        re.compile(
            r'almost\s+(?:reached\s+)?(?:statistical\s+)?significance',
            re.IGNORECASE,
        ),
        "almost significant — 没有'差点显著'，只有显著或不显著",
    ),
    (
        re.compile(
            r'approached?\s+(?:statistical\s+)?significance',
            re.IGNORECASE,
        ),
        "approached significance — P > 0.05 强行暗示有趋势",
    ),
    (
        re.compile(
            r'borderline\s+significant',
            re.IGNORECASE,
        ),
        "borderline significant — 临界/边界显著，暗示 P 接近 0.05",
    ),
    (
        re.compile(
            r'(?:a\s+)?trend\s+(?:towards?\s+)?(?:statistical\s+)?significance',
            re.IGNORECASE,
        ),
        "trend towards significance — 趋势≠显著",
    ),
    (
        re.compile(
            r'nominally\s+significant',
            re.IGNORECASE,
        ),
        "nominally significant — 名义显著，暗示未经多重比较校正",
    ),
]


def scan_camouflage(text: str) -> list[dict]:
    """Scan for P-value linguistic camouflage patterns.

    Returns list of {phrase, explanation, context} dicts.
    """
    hits = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern, explanation in CAMOUFLAGE_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)

            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 60)
            context = text[start:end].replace('\n', ' ').strip()

            hits.append({
                "phrase": match.group(0),
                "explanation": explanation,
                "context": f"...{context}...",
            })

    return hits


def run_pvalue_camouflage(paper) -> list[CheckResult]:
    """Scan for P-value linguistic camouflage in Results and Discussion."""
    # Search both Results and Discussion
    text = ""
    if paper.results:
        text += paper.results + "\n"
    if paper.discussion:
        text += paper.discussion

    if not text:
        text = paper.full_text

    if not text:
        return [
            CheckResult(
                check_id="text.pvalue_camouflage",
                check_name="P 值伪装话术",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    hits = scan_camouflage(text)

    evidence = []
    for h in hits:
        evidence.append(f'"{h["phrase"]}" — {h["explanation"]}')
        evidence.append(f"  上下文: {h['context']}")

    total = len(hits)

    if total >= 2:
        level = "red"
        verdict = f"命中 {total} 处 P 值伪装话术，强烈暗示系统性美化不显著结果"
    elif total == 1:
        level = "orange"
        verdict = f"命中 1 处 P 值伪装话术"
    else:
        level = "green"
        verdict = "未命中 P 值伪装话术"

    return [
        CheckResult(
            check_id="text.pvalue_camouflage",
            check_name="P 值伪装话术",
            level=level,
            verdict=verdict,
            evidence=evidence[:20],
            confidence=0.85,
            needs_human=total > 0,
            human_instruction="找到原文对应位置，核实该处报告的 P 值是否确实 > 0.05。"
            "如果 Results 中报告 P > 0.05，但 Discussion 中使用这些话术，则为统计违规。",
        )
    ]
