"""Statistical fallacy detection.

Detects common logical errors in statistical reasoning:
  1. "No significant difference" → "therefore equivalent" fallacy
     (confusing absence of evidence with evidence of absence)
  2. "P > 0.05" → "non-toxic" / "safe" / "no effect" fallacy
  3. "No significant difference" → selection of dose/concentration
     (e.g., "P > 0.05, therefore this concentration was non-toxic")
"""

from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation


FALLACY_PATTERNS = [
    # Type 1: No sig diff → "therefore" conclusion
    (
        re.compile(
            r'(?:no\s+significant\s+difference|P\s*>\s*0\.05|not\s+(?:statistically\s+)?significant)'
            r'(?:[^.]*?)(?:therefore|thus|hence|indicating|suggesting|demonstrating|'
            r'confirming|so\s+(?:that|the))',
            re.IGNORECASE,
        ),
        "red",
        "无显著差异 → 因此/所以/表明... — 经典'无证据≠无效应'谬误。"
        "P > 0.05 只能说明'未检测到差异'，不能证明'无差异'或'等效'。",
    ),
    # Type 2: P > 0.05 → non-toxic
    (
        re.compile(
            r'(?:P\s*>\s*0\.05|no\s+significant\s+difference|not\s+significantly\s+different)'
            r'(?:[^.]*?)(?:non[.-]toxic|non[.-]cytotoxic|safe|without\s+toxicity|'
            r'no\s+(?:cytotoxic|toxic)\s+effect|not\s+toxic)',
            re.IGNORECASE,
        ),
        "red",
        "P > 0.05 → 无毒/安全 — 统计学谬误。等价性检验需要预设等价边界（如 TOST），"
        "不能仅凭'无显著差异'推断安全性或等效性。",
    ),
    # Type 3: P > 0.05 → chosen as dose
    (
        re.compile(
            r'(?:P\s*>\s*0\.05|no\s+significant\s+difference|not\s+significantly\s+different)'
            r'(?:[^.]*?)(?:was\s+(?:selected|chosen|used)|therefore\s+(?:selected|chosen|used)|'
            r'concentration\s+(?:was\s+)?(?:selected|chosen))',
            re.IGNORECASE,
        ),
        "orange",
        "P > 0.05 → 选定该浓度 — 可能犯了'接受零假设'的错误。"
        "无显著差异≠无效应（特别是小样本下），选择'最高无毒浓度'需要更严格的等效性证明。",
    ),
    # Type 4: Absence of evidence language
    (
        re.compile(
            r'(?:the\s+)?(?:results|data|findings)\s+(?:demonstrate|confirm|prove|show|indicate)'
            r'\s+(?:that\s+)?(?:there\s+(?:is|are|was|were)\s+)?no\s+(?:significant\s+)?'
            r'(?:difference|effect|change|impact|influence|toxicity)',
            re.IGNORECASE,
        ),
        "yellow",
        "声称'结果证明无差异/无效应' — 统计检验不能证明零假设。"
        "应表述为'未观察到显著差异'而非'证明无差异'。",
    ),
]


def run_fallacies(paper) -> list[CheckResult]:
    """Detect statistical fallacies in Results and Discussion."""
    text = ""
    if paper.results:
        text += paper.results + "\n"
    if paper.discussion:
        text += paper.discussion

    if not text:
        text = paper.methods or paper.full_text

    if not text:
        return [
            CheckResult(
                check_id="stats.fallacies",
                check_name="统计推断谬误检测",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    all_hits = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern, severity, explanation in FALLACY_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 100)
            context = text[start:end].replace('\n', ' ').strip()
            all_hits.append({
                "matched": match.group(0)[:120],
                "severity": severity,
                "explanation": explanation,
                "context": context,
            })

    evidence = []
    for h in all_hits[:12]:
        evidence.append(f"[{h['severity'].upper()}] \"{h['matched']}\"")
        evidence.append(f"  → {h['explanation']}")
        evidence.append(f"  上下文: ...{h['context']}...")

    red_hits = [h for h in all_hits if h["severity"] == "red"]
    orange_hits = [h for h in all_hits if h["severity"] == "orange"]
    yellow_hits = [h for h in all_hits if h["severity"] == "yellow"]

    if red_hits:
        level = "red"
        verdict = (
            f"发现 {len(red_hits)} 处严重统计推断谬误"
            + (f" + {len(orange_hits)} 处可疑推断" if orange_hits else "")
        )
    elif orange_hits:
        level = "orange"
        verdict = f"发现 {len(orange_hits)} 处统计推断谬误"
    elif yellow_hits:
        level = "yellow"
        verdict = f"发现 {len(yellow_hits)} 处统计表述不规范"
    else:
        level = "green"
        verdict = "未发现统计推断谬误"

    return [
        CheckResult(
            check_id="stats.fallacies",
            check_name="统计推断谬误检测",
            level=level,
            verdict=verdict,
            evidence=evidence[:15],
            confidence=0.85,
            needs_human=len(all_hits) > 0,
            human_instruction="核查每个命中处的统计推断逻辑。"
            "'无显著差异'不能推断为'无效应'或'等效'。若需证明等效性，需使用 TOST 等等价性检验。"
            "特别关注：用小样本（n < 10）得出'无毒/安全'结论的情况。",
        )
    ]
