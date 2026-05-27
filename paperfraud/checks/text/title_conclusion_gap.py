"""Title/Abstract claim vs. Results statistical reality gap detection.

Detects when a paper's title or abstract makes efficacy claims that are
contradicted by the actual statistical results reported in the Results section.
"""

from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation


# Efficacy claim verbs commonly found in titles/abstracts
EFFICACY_PATTERNS = [
    (re.compile(r'\b(enhance[sd]?|enhancing)\b', re.IGNORECASE), "enhance"),
    (re.compile(r'\b(improve[sd]?|improving)\b', re.IGNORECASE), "improve"),
    (re.compile(r'\b(inhibit[sd]?|inhibiting|inhibition)\b', re.IGNORECASE), "inhibit"),
    (re.compile(r'\b(suppress[esd]?|suppressing|suppression)\b', re.IGNORECASE), "suppress"),
    (re.compile(r'\b(reduce[sd]?|reducing|reduction)\b', re.IGNORECASE), "reduce"),
    (re.compile(r'\b(overcome[sd]?|overcoming)\b', re.IGNORECASE), "overcome"),
    (re.compile(r'\b(restore[sd]?|restoring|restoration)\b', re.IGNORECASE), "restore"),
    (re.compile(r'\b(rescue[sd]?|rescuing)\b', re.IGNORECASE), "rescue"),
    (re.compile(r'\b(promote[sd]?|promoting|promotion)\b', re.IGNORECASE), "promote"),
    (re.compile(r'\b(synergi(?:ze[sd]?|stic|sm))\b', re.IGNORECASE), "synergize"),
]

# Null-result patterns in Results section
NULL_RESULT_PATTERNS = [
    re.compile(r'no\s+significant\s+difference', re.IGNORECASE),
    re.compile(r'not\s+(?:statistically\s+)?significant', re.IGNORECASE),
    re.compile(r'P\s*>\s*0\.05', re.IGNORECASE),
    re.compile(r'did\s+not\s+(?:reach|achieve|attain)\s+(?:statistical\s+)?significance', re.IGNORECASE),
    re.compile(r'failed\s+to\s+(?:reach|achieve|show)\s+(?:statistical\s+)?significance', re.IGNORECASE),
    re.compile(r'comparable\s+between', re.IGNORECASE),
    re.compile(r'no\s+significant\s+(?:improvement|enhancement|benefit|effect|change|difference)', re.IGNORECASE),
    re.compile(r'with\s+no\s+significant\s+difference', re.IGNORECASE),
]

# Antiviral response / mechanistic endpoints (not efficacy)
MECHANISM_ONLY_PATTERNS = [
    re.compile(r'viral\s+(?:replication|load|titer)', re.IGNORECASE),
    re.compile(r'intratumoral\s+(?:virus|viral|NDV)', re.IGNORECASE),
    re.compile(r'IFN.*(?:release|production|expression)', re.IGNORECASE),
    re.compile(r'JAK.?(?:STAT)', re.IGNORECASE),
]


def _extract_efficacy_claims(text: str) -> list[dict]:
    """Find efficacy claims in text."""
    claims = []
    seen_spans: set[tuple[int, int]] = set()
    for pattern, verb_type in EFFICACY_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 60)
            claims.append({
                "verb": match.group(0),
                "type": verb_type,
                "context": text[start:end].replace('\n', ' ').strip(),
            })
    return claims


def _find_null_results(text: str) -> list[str]:
    """Find null result statements in Results section."""
    null_results = []
    for pattern in NULL_RESULT_PATTERNS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 80)
            null_results.append(text[start:end].replace('\n', ' ').strip())
    return null_results


def _classify_gap(
    title_claims: list[dict],
    abstract_claims: list[dict],
    null_results: list[str],
) -> dict:
    """Determine if there's a gap between claims and statistical reality."""
    title_verb_types = {c["type"] for c in title_claims}
    abstract_verb_types = {c["type"] for c in abstract_claims}

    efficacy_types = {"enhance", "improve", "overcome", "synergize"}
    title_efficacy = title_verb_types & efficacy_types
    abstract_efficacy = abstract_verb_types & efficacy_types

    has_null = len(null_results) > 0

    # Check if null results relate to a primary endpoint vs secondary/mechanistic
    primary_null = []
    mechanistic_null = []
    for nr in null_results:
        is_mechanistic = any(p.search(nr) for p in MECHANISM_ONLY_PATTERNS)
        if is_mechanistic:
            mechanistic_null.append(nr)
        else:
            primary_null.append(nr)

    gap_signals = []
    if title_efficacy and has_null:
        gap_signals.append(
            f"标题使用疗效动词 {title_efficacy}，但 Results 中存在 {len(null_results)} 处"
            f"无显著差异表述（其中 {len(primary_null)} 处涉及主要终点）"
        )
    if abstract_efficacy and primary_null:
        gap_signals.append(
            f"Abstract 声称疗效提升，但主要终点无显著差异"
        )

    return {
        "title_efficacy_types": list(title_efficacy),
        "abstract_efficacy_types": list(abstract_efficacy),
        "null_result_count": len(null_results),
        "primary_null_count": len(primary_null),
        "mechanistic_null_count": len(mechanistic_null),
        "gap_signals": gap_signals,
        "null_results": null_results[:8],
        "has_gap": len(gap_signals) > 0,
    }


def run_title_conclusion_gap(paper) -> list[CheckResult]:
    """Check for discrepancies between title/abstract claims and statistical results."""
    title = paper.title or ""
    abstract = paper.abstract or ""
    results_text = paper.results or ""
    discussion = paper.discussion or ""

    if not results_text:
        return [
            CheckResult(
                check_id="text.title_conclusion_gap",
                check_name="标题-结论一致性",
                level="error",
                verdict="无法执行：未提取到 Results 文本",
                needs_human=False,
            )
        ]

    title_claims = _extract_efficacy_claims(title)
    abstract_claims = _extract_efficacy_claims(abstract)
    null_results = _find_null_results(results_text)
    # Also search discussion for acknowledged null results
    discussion_null = _find_null_results(discussion)
    all_null = null_results + discussion_null

    classification = _classify_gap(title_claims, abstract_claims, all_null)

    evidence = []
    if title_claims:
        evidence.append(f"标题中的疗效/因果动词: {[c['verb'] for c in title_claims]}")
    if abstract_claims:
        evidence.append(f"Abstract 中疗效动词: {[c['verb'] for c in abstract_claims][:5]}")
    for signal in classification["gap_signals"]:
        evidence.append(f"GAP 信号: {signal}")
    for nr in classification["null_results"][:5]:
        evidence.append(f"无显著差异: ...{nr}...")

    if not classification["has_gap"]:
        return [
            CheckResult(
                check_id="text.title_conclusion_gap",
                check_name="标题-结论一致性",
                level="green",
                verdict="标题和 Abstract 中的疗效声称与 Results 统计结果基本一致",
                evidence=evidence[:10],
                confidence=0.7,
                needs_human=False,
            )
        ]

    # Classify severity
    primary_null = classification["primary_null_count"]
    if primary_null >= 2:
        level = "red"
        verdict = (
            f"严重矛盾：标题/Abstract 声称疗效提升，但 {primary_null} 处主要终点无显著差异"
        )
    elif primary_null == 1:
        level = "orange"
        verdict = "标题/Abstract 的疗效声称与至少 1 处主要终点无显著差异矛盾"
    else:
        level = "yellow"
        verdict = (
            f"标题/Abstract 声称疗效，但 {classification['mechanistic_null_count']} 处"
            f"次要/机制终点无显著差异 — 需人工判断是否为合理推断"
        )

    return [
        CheckResult(
            check_id="text.title_conclusion_gap",
            check_name="标题-结论一致性",
            level=level,
            verdict=verdict,
            evidence=evidence[:15],
            source_locations=[
                SourceLocation(page=1, excerpt=c["context"]) for c in title_claims[:3]
            ],
            confidence=0.75,
            needs_human=True,
            human_instruction="仔细比对标题/Abstract 中的动词与 Results 中的统计检验结果。"
            "若标题宣称'增强疗效'但体内主要终点 P > 0.05，属于严重结论夸大。"
            "区分主要疗效终点 vs 机制性替代终点（如病毒载量增加 ≠ 疗效提升）。",
        )
    ]
