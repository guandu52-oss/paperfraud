"""Blacklist word scanning for overclaim and hype detection.

Flags inflated language common in fabricated or paper-mill papers.
Word lists are loaded from blacklist.yaml with Python hardcoded fallback.
"""

from __future__ import annotations

import re
from pathlib import Path

from paperfraud.base import CheckResult, SourceLocation


def _load_blacklist_terms() -> dict[str, list[str]]:
    """Load blacklist terms from YAML config, falling back to hardcoded defaults.

    Merges two sources:
      1. Static: checks/text/blacklist.yaml (curated by developers)
      2. Dynamic: paperfraud_data/blacklist.yaml (crawler + LLM + human review)
    """
    import yaml

    _STATIC_PATH = Path(__file__).resolve().parent / "blacklist.yaml"
    _DYNAMIC_PATH = Path(__file__).resolve().parent.parent.parent.parent / "paperfraud_data" / "blacklist.yaml"

    result = {
        "absolute": [],
        "overclaim": [],
        "inflated": [],
    }

    # Load static rules
    try:
        if _STATIC_PATH.exists():
            data = yaml.safe_load(_STATIC_PATH.read_text(encoding="utf-8"))
            result["absolute"] = list(data.get("absolute", []))
            result["overclaim"] = list(data.get("overclaim", []))
            result["inflated"] = list(data.get("inflated", []))
    except Exception:
        pass

    # Merge dynamic rules from crawler output
    try:
        if _DYNAMIC_PATH.exists():
            data = yaml.safe_load(_DYNAMIC_PATH.read_text(encoding="utf-8"))
            for p in data.get("patterns", []):
                pattern = p.get("pattern", "")
                if pattern:
                    # Dynamic blacklist patterns go into 'inflated' category
                    # since they're paper-mill clichés / camouflage phrases
                    if pattern not in result["inflated"]:
                        result["inflated"].append(pattern)
    except Exception:
        pass

    if any(result.values()):
        return result

    # Hardcoded fallback
    return {
        "absolute": [
            "definitively", "unequivocally", "undoubtedly", "absolutely",
            "certainly", "incontrovertibly", "irrefutably",
        ],
        "overclaim": [
            "novel", "first.?time", "first.?report", "first.?evidence",
            "breakthrough", "revolutionary", "game.?changing",
            "paradigm.?shift", "unprecedented",
        ],
        "inflated": [
            "remarkable", "excellent", "outstanding", "extraordinary",
            "tremendous", "exceptional", "superb", "fantastic",
        ],
    }


_TERMS = _load_blacklist_terms()
ABSOLUTE_TERMS = _TERMS["absolute"]
OVERCLAIM_TERMS = _TERMS["overclaim"]
INFLATED_TERMS = _TERMS["inflated"]
ALL_BLACKLIST = ABSOLUTE_TERMS + OVERCLAIM_TERMS + INFLATED_TERMS

BLACKLIST_RES = [
    (re.compile(r'\b' + term + r'\b', re.IGNORECASE), term, category)
    for term, category in (
        [(t, "绝对化表述") for t in ABSOLUTE_TERMS]
        + [(t, "过度宣称") for t in OVERCLAIM_TERMS]
        + [(t, "夸大修饰") for t in INFLATED_TERMS]
    )
]


def scan_blacklist(text: str) -> list[dict]:
    """Scan text for blacklisted terms.

    Returns list of {term, category, context} dicts.
    """
    hits = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern, term, category in BLACKLIST_RES:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)

            # Extract surrounding context (50 chars each side)
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            context = text[start:end].replace('\n', ' ').strip()

            hits.append({
                "term": match.group(0),
                "category": category,
                "context": context,
                "position": match.start(),
            })

    return hits


def run_blacklist(paper) -> list[CheckResult]:
    """Run blacklist word scan on discussion and abstract."""
    text = paper.discussion or paper.abstract or paper.full_text
    if not text:
        return [
            CheckResult(
                check_id="text.blacklist",
                check_name="黑名单词扫描",
                level="error",
                verdict="无法执行：未提取到 Discussion/Abstract 文本",
                needs_human=False,
            )
        ]

    hits = scan_blacklist(text)

    category_counts: dict[str, int] = {}
    for h in hits:
        category_counts[h["category"]] = category_counts.get(h["category"], 0) + 1

    evidence = []
    for h in hits[:20]:  # Cap at 20 for readability
        evidence.append(f'[{h["category"]}] "{h["term"]}": ...{h["context"]}...')

    total = len(hits)

    if total >= 5:
        level = "orange"
        verdict = f"命中 {total} 个黑名单/夸大词汇（{category_counts}）"
    elif total >= 3:
        level = "yellow"
        verdict = f"命中 {total} 个黑名单/夸大词汇（{category_counts}）"
    elif total > 0:
        level = "green"
        verdict = f"命中 {total} 个词汇，尚在正常范围"
    else:
        level = "green"
        verdict = "未命中黑名单词汇"

    return [
        CheckResult(
            check_id="text.blacklist",
            check_name="黑名单词扫描",
            level=level,
            verdict=verdict,
            evidence=evidence[:10],
            source_locations=[SourceLocation(page=1, excerpt=h["context"]) for h in hits[:5]],
            confidence=0.7,
            needs_human=total >= 3,
            human_instruction="逐条检查命中词汇的上下文。某些领域'novel'为常规用词，需结合学科判断。",
        )
    ]
