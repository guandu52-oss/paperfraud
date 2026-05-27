"""Statistical method misuse detection.

Detects inappropriate statistical test choices in life science papers:
  1. t-test used for >2 groups (should use ANOVA)
  2. t-test used for time-series / repeated-measures data (should use RM-ANOVA)
  3. Multiple pairwise t-tests without correction (multiple comparison problem)
  4. Methods only mentions t-test, but paper has 3+ experimental groups
  5. Excessive t-test usage with no ANOVA/correction (aggregate signal)
"""
from __future__ import annotations

import re

from paperfraud.base import CheckResult, SourceLocation


# ── t-test declaration patterns (handles Unicode apostrophe) ────────────────
T_TEST_DECLARATION = re.compile(
    r"(?:unpaired |paired |two.tailed |two.sided |independent )?"
    r"(?:t[ .-]test|t test|Student.{0,2}s? t[ .-]test)",
    re.IGNORECASE,
)

# Group counting patterns (3+ groups)
MULTI_GROUP = re.compile(
    r"(?:three|four|five|six|seven|eight|nine|ten|\d+) "
    r"(?:groups?|experimental groups?|treatment groups?|conditions?|cohorts?)",
    re.IGNORECASE,
)

# Time-course / repeated measures indicators
TIME_SERIES = re.compile(
    r"time[ .-]point|time[ .-]course|time[ .-]series|time[ .-]dependent|"
    r"repeated measures?|longitudinal|"
    r"baseline.{0,30}(?:and|vs|versus|compared).{0,30}post|"
    r"at (?:day|week|month|hour)s? \d+",
    re.IGNORECASE,
)

# Multiple pairwise without correction
PAIRWISE_WITHOUT_CORRECTION = re.compile(
    r"(?:each|all|multiple|several) (?:pair|group|condition)s? "
    r"(?:were|was) (?:compared|analyzed|tested) (?:using|with|by) (?:a )?t[ .-]test|"
    r"t[ .-]tests? (?:were|was) (?:used|performed|conducted|applied) "
    r"(?:to compare|for) (?:each|all|multiple) (?:pair|comparison)",
    re.IGNORECASE,
)

# Pairwise comparison language (weaker signal — "between X and Y" patterns)
PAIRWISE_COMPARISONS = re.compile(
    r"(?:compared|comparison)\s+(?:between|among|across)\s+(?:groups?|conditions?|treatments?)",
    re.IGNORECASE,
)

# ANOVA mention
ANOVA_MENTION = re.compile(
    r"ANOVA|analysis of variance|"
    r"one.way ANOVA|two.way ANOVA|three.way ANOVA|"
    r"Kruskal.Wallis|Friedman|"
    r"repeated.measures? ANOVA|RM.ANOVA",
    re.IGNORECASE,
)

# Multiple comparison correction
CORRECTION_MENTION = re.compile(
    r"Bonferroni|Tukey|Dunnett|Sidak|Holm|"
    r"FDR|false discovery rate|"
    r"multiple.comparison.correction|"
    r"post.hoc|posthoc",
    re.IGNORECASE,
)

# Non-parametric test mentions
NONPARAMETRIC_MENTION = re.compile(
    r"Mann.Whitney|Wilcoxon|Kruskal.Wallis|Friedman|"
    r"Spearman|non.parametric|nonparametric|rank.sum",
    re.IGNORECASE,
)

# Experimental group labels — detects patterns like "X group", "X-treated group"
GROUP_LABEL = re.compile(
    r"(?:control|treatment|experimental|vehicle|sham|WT|KO|wild.type|knockout|"
    r"transgenic|mutant|deleted|silenced|overexpressed?)\s+"
    r"(?:group|mice|rats?|cells?|animals?|samples?)|"
    r"(?:group|mice|rats?|cells?)\s+(?:were|was|received|treated|injected|fed)",
    re.IGNORECASE,
)


def run_method_misuse(paper) -> list[CheckResult]:
    """Detect statistical method misuse in a paper.

    Scans full text for t-test declarations, then checks
    whether the experimental design justifies the test choice.
    """
    methods = paper.methods or ""
    full_text = paper.full_text or ""

    if not full_text:
        return [
            CheckResult(
                check_id="stats.method_misuse",
                check_name="统计方法误用检测",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    has_anova = bool(ANOVA_MENTION.search(full_text))
    has_correction = bool(CORRECTION_MENTION.search(full_text))
    has_nonparametric = bool(NONPARAMETRIC_MENTION.search(full_text))

    evidence = []
    red_flags = []
    orange_flags = []
    locations = []

    t_test_contexts = list(T_TEST_DECLARATION.finditer(full_text))
    multi_group_contexts = list(MULTI_GROUP.finditer(full_text))
    time_hits = list(TIME_SERIES.finditer(full_text))
    pw_hits = list(PAIRWISE_WITHOUT_CORRECTION.finditer(full_text))

    # ── 1. t-test used for >2 groups ────────────────────────────────────
    if t_test_contexts and multi_group_contexts:
        for tg in t_test_contexts:
            t_pos = tg.start()
            for mg in multi_group_contexts:
                if abs(t_pos - mg.start()) < 500:
                    ctx_start = max(0, min(t_pos, mg.start()) - 80)
                    ctx_end = min(len(full_text), max(tg.end(), mg.end()) + 80)
                    ctx = full_text[ctx_start:ctx_end].replace("\n", " ").strip()
                    red_flags.append(
                        f"t 检验用于多组比较：'{tg.group(0).strip()}' "
                        f"附近提到 '{mg.group(0).strip()}'"
                    )
                    evidence.append(f"上下文: \"...{ctx[:250]}...\"")
                    locations.append(SourceLocation(
                        page=1, excerpt=ctx[:200]
                    ))
                    break

    # ── 2. t-test used for time-series / repeated measures ──────────────
    if t_test_contexts and time_hits:
        for tg in t_test_contexts:
            t_pos = tg.start()
            for th in time_hits:
                if abs(t_pos - th.start()) < 600:
                    ctx_start = max(0, min(t_pos, th.start()) - 80)
                    ctx_end = min(len(full_text), max(tg.end(), th.end()) + 80)
                    ctx = full_text[ctx_start:ctx_end].replace("\n", " ").strip()
                    red_flags.append(
                        f"t 检验用于重复测量/时间序列数据："
                        f"'{tg.group(0).strip()}' 附近检测到时间序列指标"
                    )
                    evidence.append(f"上下文: \"...{ctx[:250]}...\"")
                    locations.append(SourceLocation(
                        page=1, excerpt=ctx[:200]
                    ))
                    break

    # ── 3. Multiple pairwise without correction ─────────────────────────
    if pw_hits and not has_correction:
        for pw in pw_hits:
            ctx_start = max(0, pw.start() - 80)
            ctx_end = min(len(full_text), pw.end() + 80)
            ctx = full_text[ctx_start:ctx_end].replace("\n", " ").strip()
            if has_anova:
                orange_flags.append(
                    f"多重两两比较未提及校正：'{pw.group(0).strip()[:150]}' "
                    f"（但全文提到了 ANOVA，可能已处理）"
                )
            else:
                red_flags.append(
                    f"多重两两比较未提及多重比较校正："
                    f"'{pw.group(0).strip()[:150]}'"
                )
            evidence.append(f"上下文: \"...{ctx[:250]}...\"")
            locations.append(SourceLocation(
                page=1, excerpt=ctx[:200]
            ))
    elif pw_hits and has_correction:
        evidence.append(
            "检测到多重两两比较，但论文提到了校正方法，可接受"
        )

    # ── 4. t-test used but paper has 3+ groups in results ──────────────
    # Even if Methods doesn't explicitly say "t-test for all comparisons",
    # we check if (a) Methods only mentions t-test, (b) never mentions ANOVA,
    # and (c) full text has 3+ groups
    if t_test_contexts and not has_anova:
        group_nums = []
        for mg in multi_group_contexts:
            num_str = mg.group(0).split()[0].lower()
            num_map = {"three": 3, "four": 4, "five": 5, "six": 6,
                       "seven": 7, "eight": 8, "nine": 9, "ten": 10}
            n = num_map.get(num_str, None)
            if n is None:
                try:
                    n = int(num_str)
                except ValueError:
                    continue
            if n >= 3:
                group_nums.append((n, mg))

        if group_nums:
            group_nums.sort(key=lambda x: x[0], reverse=True)
            max_n, max_mg = group_nums[0]
            ctx_start = max(0, max_mg.start() - 60)
            ctx_end = min(len(full_text), max_mg.end() + 60)
            ctx = full_text[ctx_start:ctx_end].replace("\n", " ").strip()
            orange_flags.append(
                f"Methods 仅声明 t 检验但未提 ANOVA，全文提到 {max_n} 个组——"
                f"比较 ≥3 组应使用 ANOVA 而非 t 检验"
            )
            evidence.append(
                f"多组上下文: \"...{ctx[:200]}...\""
            )

    # ── 5. Excessive t-tests without ANOVA or correction ───────────────
    # If a paper uses t-tests in many figures but never mentions ANOVA or
    # correction, the aggregate pattern itself is suspicious — even if each
    # individual comparison is "two groups", the overall design inflates
    # Type I error across the family of comparisons.
    n_t_tests = len(t_test_contexts)
    if n_t_tests >= 5 and not has_anova and not has_correction:
        # Extract a few example contexts
        example_contexts = []
        for tg in t_test_contexts[:3]:
            ctx_start = max(0, tg.start() - 60)
            ctx_end = min(len(full_text), tg.end() + 60)
            ctx = full_text[ctx_start:ctx_end].replace("\n", " ").strip()
            example_contexts.append(ctx[:150])

        alt_methods = "或非参数检验（Mann-Whitney/Wilcoxon）" if not has_nonparametric else ""
        red_flags.append(
            f"全文 {n_t_tests} 处 t 检验声明，但未提及 ANOVA、多重比较校正"
            f"{alt_methods}——大量两两比较导致 I 类错误膨胀"
        )
        evidence.append(
            f"全文共 {n_t_tests} 处 t 检验，无 ANOVA/校正。"
            f"示例: {' | '.join(example_contexts[:2])}"
        )
        # Add locations from first few t-test hits
        for tg in t_test_contexts[:3]:
            ctx_start = max(0, tg.start() - 40)
            ctx_end = min(len(full_text), tg.end() + 40)
            ctx = full_text[ctx_start:ctx_end].replace("\n", " ").strip()
            locations.append(SourceLocation(page=1, excerpt=ctx[:200]))

    # ── 6. "Two groups" language but many distinct experimental labels ──
    # Papers often say "two groups were compared with t-test" but the
    # actual paper has many different pairs of groups being compared.
    if n_t_tests >= 3 and not has_anova and not has_correction and not red_flags:
        # Count distinct group label mentions as a heuristic for
        # "how many different comparisons are being made"
        group_labels_found = set()
        for gl in GROUP_LABEL.finditer(full_text):
            group_labels_found.add(gl.group(0).strip().lower())

        n_labels = len(group_labels_found)
        if n_labels >= 5:
            orange_flags.append(
                f"Methods 仅用 t 检验比较'两组'，但全文检测到 {n_labels} 种"
                f"不同实验条件标签——建议使用 ANOVA 进行整体比较"
            )
            evidence.append(
                f"检测到的组标签 ({n_labels} 种): "
                f"{', '.join(sorted(group_labels_found)[:10])}"
            )

    # ── Decision ────────────────────────────────────────────────────────
    if red_flags:
        level = "red"
        verdict = (
            f"发现 {len(red_flags)} 处统计方法误用：{'；'.join(red_flags[:3])}"
        )
    elif orange_flags:
        level = "orange"
        verdict = (
            f"发现 {len(orange_flags)} 处理论严重度较低的方法学问题："
            f"{'；'.join(orange_flags[:3])}"
        )
    elif t_test_contexts:
        level = "green"
        verdict = (
            f"检测到 {len(t_test_contexts)} 处 t 检验声明，"
            f"未发现明显方法误用"
        )
        if has_anova:
            verdict += "（论文使用了 ANOVA）"
        if has_correction:
            verdict += "（提及了多重比较校正）"
    else:
        level = "green"
        verdict = "未检测到 t 检验声明或方法学误用模式"

    result = CheckResult(
        check_id="stats.method_misuse",
        check_name="统计方法误用检测",
        level=level,
        verdict=verdict,
        evidence=evidence[:10],
        source_locations=locations,
        confidence=0.75,
        needs_human=level != "green",
        human_instruction=(
            "统计方法选择需匹配实验设计：\n"
            "1. 比较 ≥3 组应使用 ANOVA（或非参数 Kruskal-Wallis）而非 t 检验\n"
            "2. 时间序列/重复测量数据应使用 RM-ANOVA 或混合效应模型\n"
            "3. 多重两两比较必须使用 Bonferroni/Tukey/FDR 等校正\n"
            "4. 全文多处使用 t 检验但从未提及 ANOVA/校正 → 检查是否应该使用 ANOVA\n"
            "5. 两独立组 + 单次测量 → t 检验正确\n"
            "6. 核实论文中是否在其他段落提到了 ANOVA/校正方法"
        ),
    )

    return [result]
