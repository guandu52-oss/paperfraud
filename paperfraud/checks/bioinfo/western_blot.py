"""Western blot loading control checker.

Detects whether papers using Western blot mention a loading control.

Why this matters:
  - Western blot band intensity is meaningless without a loading control
    (β-actin, GAPDH, α-tubulin, etc.) to normalize against
  - Loading controls are non-negotiable in experimental biology
  - Missing loading control is either a major methods omission or
    intentional data manipulation (can't normalize = can fabricate)
  - Reusing the same loading control image across figures = fraud

This is the kind of domain rule that only someone who has run Western blots
would know to check for. Pure NLP engineers miss this entirely.
"""

from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation

# Detection: paper uses Western blot / immunoblot
WB_KEYWORDS = re.compile(
    r'(?:western\s*blot|immunoblot|immuno[.-]?blot|WB\b|immunodetection|'
    r'ECL\b|chemiluminescen|PVDF|nitrocellulose|SDS[.-]?PAGE|'
    r'protein\s*(?:band|expression|level).{0,30}?(?:blot|membrane))',
    re.IGNORECASE,
)

# Loading control mentions
LOADING_CONTROL = re.compile(
    r'(?:loading\s*control|internal\s*control|house[.-]?keeping|'
    r'reference\s*(?:protein|gene)|endogenous\s*control|'
    r'β[.-]?actin|beta[.-]?actin|GAPDH|α[.-]?tubulin|alpha[.-]?tubulin|'
    r'β[.-]?tubulin|beta[.-]?tubulin|vinculin|HPRT|18S|28S|'
    r'actin\b|tubulin\b|lamin\b|cyclophilin|TBP\b|'
    r'ponceau|coomassie|total\s*protein\s*(?:stain|normalization))',
    re.IGNORECASE,
)

# Normalization methods (some papers use total protein staining instead of single protein)
TOTAL_PROTEIN_NORM = re.compile(
    r'(?:total\s*protein\s*(?:stain|normalization|loading)|'
    r'stain[.-]?free|revert|ponceau\s*S|coomassie\s*blue)',
    re.IGNORECASE,
)

# Figure reference to WB
WB_FIGURES = re.compile(
    r'(?:western\s*blot|immunoblot).{0,50}?(?:Fig|Figure|Fig.)\s*\d+',
    re.IGNORECASE,
)


def run_western_blot_check(paper) -> list[CheckResult]:
    """Check Western blot methodology completeness."""
    text = paper.full_text or ""
    methods = paper.methods or ""

    if not text:
        return [
            CheckResult(
                check_id="bioinfo.western_blot",
                check_name="Western Blot 上样量对照校验",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    # Step 1: Is Western blot even mentioned?
    wb_mentions = list(WB_KEYWORDS.finditer(text))
    if not wb_mentions:
        return [
            CheckResult(
                check_id="bioinfo.western_blot",
                check_name="Western Blot 上样量对照校验",
                level="green",
                verdict="未检测到 Western blot 实验方法",
                evidence=["本文可能未使用 Western blot 技术。"],
                confidence=0.9,
                needs_human=False,
            )
        ]

    # Step 2: Check for loading control in Methods
    has_loading = bool(LOADING_CONTROL.search(methods))
    has_total_protein = bool(TOTAL_PROTEIN_NORM.search(methods))

    # Also search full text for loading control (with proximity to WB mentions)
    has_loading_full = bool(LOADING_CONTROL.search(text))

    # Step 3: Build evidence
    evidence = []
    for m in wb_mentions[:5]:
        start = max(0, m.start() - 20)
        end = min(len(text), m.end() + 60)
        ctx = text[start:end].replace('\n', ' ').strip()
        evidence.append(f"WB 提及: \"...{ctx}...\"")

    if has_total_protein:
        evidence.append("✓ 提及了总蛋白染色法（total protein stain）作为上样量对照。")

    if has_loading or has_loading_full:
        lc_hits = list(LOADING_CONTROL.finditer(methods or text))
        for h in lc_hits[:3]:
            evidence.append(f"✓ 上样量对照: \"{h.group(0)}\"")
        level = "green"
        verdict = (
            f"论文使用了 Western blot 并提及上样量对照/内参蛋白"
            f"{'（总蛋白染色法）' if has_total_protein else ''}"
        )
        confidence = 0.95
        needs_human = False
        human_instruction = ""
    else:
        level = "red"
        verdict = (
            f"论文在 {len(wb_mentions)} 处提及 Western blot / immunoblot，"
            f"但 Methods 中未发现上样量对照（loading control）声明。"
            f"未提及 β-actin、GAPDH、tubulin 等内参蛋白或总蛋白染色法。"
        )
        confidence = 0.92
        needs_human = True
        human_instruction = (
            "Western blot 条带强度必须用上样量对照（loading control）归一化。"
            "无上样量对照意味着：1) 方法学不完整 或 2) 条带无法被独立验证。"
            "常见内参：β-actin、GAPDH、α-tubulin、vinculin、ponceau S 总蛋白染色。"
            "检查图中是否出现 loading control 条带，以及同一条 loading control "
            "是否跨图重复使用（经典 WB 造假手法）。"
        )
        evidence.append("⚠️ 未发现 β-actin、GAPDH、tubulin、vinculin、ponceau S 等内参蛋白声明。")

    return [
        CheckResult(
            check_id="bioinfo.western_blot",
            check_name="Western Blot 上样量对照校验",
            level=level,
            verdict=verdict,
            evidence=evidence[:10],
            source_locations=[
                SourceLocation(page=1, excerpt=e.split('"')[1] if '"' in e else e)
                for e in evidence[:3]
            ],
            confidence=confidence,
            needs_human=needs_human,
            human_instruction=human_instruction,
        )
    ]
