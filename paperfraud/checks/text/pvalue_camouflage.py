"""P-value linguistic camouflage detection.

Scans Discussion/Results for phrases that disguise non-significant
results as significant. Patterns are loaded from camouflage.yaml with
Python hardcoded fallback.
"""

from __future__ import annotations

import re
from pathlib import Path

from paperfraud.base import CheckResult, SourceLocation


def _load_camouflage_patterns() -> list[tuple[str, str]]:
    """Load camouflage patterns from YAML config, falling back to hardcoded defaults.

    Merges two sources:
      1. Static: checks/text/camouflage.yaml (curated by developers)
      2. Dynamic: paperfraud_data/camouflage.yaml (crawler + LLM + human review)
    """
    import yaml

    _STATIC_PATH = Path(__file__).resolve().parent / "camouflage.yaml"
    _DYNAMIC_PATH = Path(__file__).resolve().parent.parent.parent.parent / "paperfraud_data" / "camouflage.yaml"

    patterns: list[tuple[str, str]] = []

    # Load static rules
    try:
        if _STATIC_PATH.exists():
            data = yaml.safe_load(_STATIC_PATH.read_text(encoding="utf-8"))
            for p in data.get("patterns", []):
                patterns.append((p["regex"], p["explanation"]))
    except Exception:
        pass

    # Merge dynamic rules from crawler output
    try:
        if _DYNAMIC_PATH.exists():
            data = yaml.safe_load(_DYNAMIC_PATH.read_text(encoding="utf-8"))
            for p in data.get("patterns", []):
                pattern = p.get("pattern", "")
                technique = p.get("technique", "")
                if pattern:
                    # Escape for regex and use as literal pattern
                    escaped = re.escape(pattern)
                    patterns.append((escaped, technique))
    except Exception:
        pass

    if patterns:
        return patterns

    return [
        (r'(?:trending|tend(?:ed|ing)?)\s+towards?\s+(?:statistical\s+)?significance',
         "trending towards significance — P > 0.05，暗示接近显著但实际不显著"),
        (r'marginally\s+significant',
         "marginally significant — 经典 P 值伪装话术，P 值通常在 0.05-0.10 之间"),
        (r'almost\s+(?:reached\s+)?(?:statistical\s+)?significance',
         "almost significant — 没有'差点显著'，只有显著或不显著"),
        (r'approached?\s+(?:statistical\s+)?significance',
         "approached significance — P > 0.05 强行暗示有趋势"),
        (r'borderline\s+significant',
         "borderline significant — 临界/边界显著，暗示 P 接近 0.05"),
        (r'(?:a\s+)?trend\s+(?:towards?\s+)?(?:statistical\s+)?significance',
         "trend towards significance — 趋势≠显著"),
        (r'nominally\s+significant',
         "nominally significant — 名义显著，暗示未经多重比较校正"),
    ]


_RAW_PATTERNS = _load_camouflage_patterns()
CAMOUFLAGE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(regex, re.IGNORECASE), explanation)
    for regex, explanation in _RAW_PATTERNS
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
