"""P-hacking / p-value distribution analyzer.

Detects statistical manipulation patterns by examining p-value distribution:

  1. Too many p-values just under 0.05 (classic p-hacking signature)
  2. Gap just above 0.05 (researcher stops when p > 0.05)
  3. Unusual clustering at common thresholds (0.01, 0.05, 0.001)

Why this matters:
  - Real p-values from honest experiments follow a roughly uniform
    or right-skewed distribution
  - P-hacked datasets show a "bump" just below 0.05 and a "dip" just above
  - This is Masicampo & Lalande (2012)'s finding: the p-curve reveals
    researcher degrees of freedom
  - 纯 CS 的人不会写这条规则——需要统计推断的领域知识
"""

from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation

# Extract all p-value references: P=0.032, P<0.05, P>0.05, p = 0.041, etc.
P_VALUE_PATTERN = re.compile(
    r'[pP]\s*([<>=≤≥])\s*(\d+[.]\d+)',
)

# "P = X" exact values are most informative for p-curve
P_EXACT = re.compile(
    r'[pP]\s*[=＝]\s*(\d+[.]\d+)',
)


def run_p_hacking_check(paper) -> list[CheckResult]:
    """Analyze p-value distribution for p-hacking signatures."""
    text = paper.full_text or ""
    if not text:
        return [
            CheckResult(
                check_id="stats.p_hacking",
                check_name="P 值分布分析 (P-hacking 检测)",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    # Extract p-values, classifying by operator
    all_hits = []
    exact_hits = []
    lt_hits = []
    gt_hits = []

    for m in P_VALUE_PATTERN.finditer(text):
        op = m.group(1)
        val = float(m.group(2))
        entry = (val, m.group(0), m.start())
        all_hits.append(entry)
        if op in ('=', '＝'):
            exact_hits.append(entry)
        elif op in ('<', '≤'):
            lt_hits.append(entry)
        elif op in ('>', '≥'):
            gt_hits.append(entry)

    exact_values = [v for v, _, _ in exact_hits]
    lt_values = [v for v, _, _ in lt_hits]
    total = len(all_hits)

    if total < 5:
        return [
            CheckResult(
                check_id="stats.p_hacking",
                check_name="P 值分布分析 (P-hacking 检测)",
                level="green",
                verdict=f"仅检测到 {total} 个 p 值，不足以进行分布分析",
                evidence=[f"p 值数量: {total}"],
                confidence=0.6,
                needs_human=False,
            )
        ]

    # ── Build evidence ────────────────────────────────────
    evidence = []
    red_flags = []
    yellow_flags = []

    evidence.append(
        f"检出 {total} 个 p 值 —— "
        f"精确值 (P=X): {len(exact_hits)}, "
        f"不等式 (P<X): {len(lt_hits)}, "
        f"不等式 (P>X): {len(gt_hits)}"
    )

    # ── Flag: No exact p-values reported ──────────────────
    if len(exact_hits) == 0 and len(lt_hits) >= 8:
        yellow_flags.append(
            f"全部 {len(lt_hits)} 个 p 值均以 'P < X' 不等式形式报告，"
            f"未提供任何精确 p 值——无法进行 p-curve 分析和独立复验。"
            f"APA/AMA 指南建议报告精确 p 值（如 P = 0.032）。"
        )

    # ── Analyze exact p-values ────────────────────────────
    if len(exact_values) >= 8:
        # p-curve bins (Masicampo & Lalande 2012 method)
        bins_exact = {
            "highly_sig": sum(1 for v in exact_values if v < 0.001),
            "p01_04": sum(1 for v in exact_values if 0.01 <= v < 0.04),
            "p04_05": sum(1 for v in exact_values if 0.04 <= v < 0.05),
            "p05_06": sum(1 for v in exact_values if 0.05 <= v < 0.06),
            "p_above_06": sum(1 for v in exact_values if v >= 0.06),
        }
        n_exact = len(exact_values)
        pct_04_05 = bins_exact["p04_05"] / n_exact * 100
        ratio = bins_exact["p04_05"] / max(1, bins_exact["p05_06"])

        evidence.append(f"精确 p 值分布 (n={n_exact}):")
        evidence.append(f"  p < 0.001: {bins_exact['highly_sig']}")
        evidence.append(f"  0.01 ≤ p < 0.04: {bins_exact['p01_04']}")
        evidence.append(f"  0.04 ≤ p < 0.05: {bins_exact['p04_05']} ({pct_04_05:.0f}%)")
        evidence.append(f"  0.05 ≤ p < 0.06: {bins_exact['p05_06']}")
        evidence.append(f"  p ≥ 0.06: {bins_exact['p_above_06']}")
        evidence.append(f"  0.04-0.05 / 0.05-0.06 比值: {ratio:.1f}")

        # Flag: p-curve bump at 0.04-0.05
        if n_exact >= 10 and pct_04_05 > 25 and ratio > 2.0:
            red_flags.append(
                f"精确 p 值在 0.04-0.05 区间占比 {pct_04_05:.0f}%，"
                f"比值 {ratio:.1f}——经典 p-hacking 信号"
            )
        elif n_exact >= 10 and pct_04_05 > 20:
            yellow_flags.append(
                f"精确 p 值在 0.04-0.05 区间占比 {pct_04_05:.0f}%"
            )

        # Flag: too few non-significant
        pct_ns = bins_exact["p_above_06"] / n_exact * 100
        if n_exact >= 15 and pct_ns < 10:
            red_flags.append(f"仅 {pct_ns:.0f}% p 值 ≥ 0.06，阴性结果异常偏低")
        elif n_exact >= 15 and pct_ns < 20:
            yellow_flags.append(f"阴性结果占比 {pct_ns:.0f}%，文件抽屉效应可疑")

        # Flag: duplicate p-values
        rounded = [round(v, 2) for v in exact_values]
        from collections import Counter
        dupes = Counter(rounded)
        common = dupes.most_common(3)
        for val, count in common:
            if count >= 4 and val >= 0.01:
                yellow_flags.append(
                    f"p ≈ {val:.2f} 出现 {count} 次——多个 p 值恰好相等极不寻常"
                )

        sample = sorted(exact_values)[:12]
        evidence.append(f"  精确值抽样: {[f'{v:.4f}' for v in sample]}")

    # ── Analyze inequality patterns ───────────────────────
    if lt_values:
        # Check common thresholds
        from collections import Counter
        thresh_counter = Counter(lt_values)
        common_thresh = thresh_counter.most_common(5)
        evidence.append(f"P < X 阈值分布: {[(f'{t:.3f}', c) for t, c in common_thresh]}")

        # P values reported as "P < 0.05" vs "P < 0.01" etc
        p05_count = sum(1 for v in lt_values if 0.049 <= v <= 0.051)
        if p05_count >= 10:
            evidence.append(
                f"  {p05_count} 个 p 值报告为 'P < 0.05'——"
                f"仅用阈值而不给精确值是过时的报告习惯"
            )

    if red_flags:
        level = "red"
        verdict = (
            f"发现 {len(red_flags)} 处 p-hacking 迹象：{'；'.join(red_flags)}"
        )
    elif yellow_flags:
        level = "yellow"
        verdict = (
            f"发现 {len(yellow_flags)} 处可疑模式：{'；'.join(yellow_flags)}"
        )
    else:
        level = "green"
        verdict = (
            f"p 值分布（{total} 个）未见明显的 p-hacking 模式"
        )

    return [
        CheckResult(
            check_id="stats.p_hacking",
            check_name="P 值分布分析 (P-hacking 检测)",
            level=level,
            verdict=verdict,
            evidence=evidence[:15],
            source_locations=[],
            confidence=0.8,
            needs_human=level != "green",
            human_instruction=(
                "P-hacking 检测基于 Masicampo & Lalande (2012) 的 p-curve 方法。"
                "健康 p 值分布应在 0-1 之间均匀或右偏，而非集中在 0.05 附近。"
                "\n\n红色信号需要人工核实：\n"
                "1. 0.04-0.05 密集——经典 p-hacking 标志（反复调整分析直到 p<0.05）\n"
                "2. p>0.05 过少——可能隐藏了阴性结果（文件抽屉效应）\n"
                "3. 多个精确相同 p 值——可能是复制粘贴或数据捏造\n"
                "\n注意：PDF 文本提取可能遗漏部分 p 值。"
                "建议用 --data-file 传入原始 Supplementary 数据复验。"
            ),
        )
    ]
