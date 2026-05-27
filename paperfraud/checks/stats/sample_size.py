"""Sample size adequacy checker.

Detects:
  1. Groups with n < 3 (statistical test validity boundary)
  2. Claims of significance with very small samples
  3. Absence of power analysis when sample sizes are small
  4. P-value plausibility: given n, what p-values are achievable?
  5. Pseudoreplication: technical replicates misused as biological replicates

Why this matters:
  - n=2: cannot compute variance meaningfully
  - n=3: t-test technically possible but power is catastrophically low
  - "n=3, p<0.001" is mathematically impossible for most biological effect sizes
  - Technical replicates (3 wells from same sample) ≠ biological replicates
  - This is a statistician-level red flag that pure-CS reviewers miss
"""

from __future__ import annotations

import math
import re

from paperfraud.base import CheckResult, SourceLocation

# ── n = X patterns ───────────────────────────────────────────────────────────
N_PATTERNS = [
    re.compile(
        r'(?:(?:each|per)\s+group\s*[,:]?\s*)?n\s*[=＝]\s*(\d+)',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:n|N)\s*[=＝]\s*(\d+)\s*(?:(?:mice|rats|dogs|animals|patients|samples|specimens|replicates?|biological|independent)\b)',
        re.IGNORECASE,
    ),
    re.compile(
        r'\(n\s*[=＝]\s*(\d+)\)',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:each|per)\s+group\s*(?:consisted of|contained|included|comprised|had)\s*(\d+)',
        re.IGNORECASE,
    ),
]

# Power analysis mention
POWER_ANALYSIS = re.compile(
    r'(?:power\s*analysis|sample\s*size\s*(?:calculation|estimation|determination)|'
    r'a\s*priori\s*(?:power|sample)|G\*Power)',
    re.IGNORECASE,
)

# ── Small sample + significant claim (suspicious) ─────────────────────────────
# Captures: n=X within ~200 chars of P < 0.0Y
SMALL_N_SIGNIFICANCE = re.compile(
    r'(?:^|(?<=\n)|(?<=\. )|(?<=; )|(?<=, ))'
    r'(?=.{0,150}?(?:[pP]\s*[<≤]\s*0[.]0(?:0[1-9]|1)))'
    r'.{0,50}?n\s*[=＝]\s*(\d+)',
    re.IGNORECASE | re.DOTALL,
)

# Simpler backup: n=X ... p<0.0Y within 200 chars
SMALL_N_P_LOOSE = re.compile(
    r'(?:n\s*[=＝]\s*(\d+).{0,100}?[pP]\s*[<≤]\s*0[.]0(?:0[1-9]|1))|'
    r'(?:[pP]\s*[<≤]\s*0[.]0(?:0[1-9]|1).{0,100}?n\s*[=＝]\s*(\d+))',
    re.IGNORECASE | re.DOTALL,
)

# ── Pseudoreplication: technical replicates ──────────────────────────────────
# n=3 from 3 wells of the SAME sample = pseudoreplication, not biological n
TECH_REPLICATE = re.compile(
    r'(?:'
    r'(?:in|performed\s+in)\s* trip licates?|triplicates?|'
    r'technical\s*replicates?|'
    r'(?:three|3)\s*(?:replicate\s*)?wells?(?:\s*per\s*(?:sample|condition))?|'
    r'(?:three|3)\s*(?:technical|replicate)\s*(?:repeats?|experiments?)|'
    r'same\s*(?:sample|specimen|lysate|RNA|cDNA|extract).{0,30}?(?:three|3|triplicat)|'
    r'each\s*(?:sample|experiment).{0,20}?(?:in|was)\s*(?:triplicat|three|3\s*(?:times|replicates?))|'
    r'performed\s*(?:in|with)\s*(?:three|3)\s*(?:independent\s*)?replicates?\s*(?:of\s*the\s*same|per\s*sample)'
    r')',
    re.IGNORECASE,
)

# ── Hidden n: non-standard n declarations ────────────────────────────────────
# "three independent experiments", "in triplicate", "n = 3 biologically independent"
HIDDEN_N = re.compile(
    r'(?:'
    r'(three|four|five|six|seven|eight|nine|ten|3|4|5|6|7|8|9|10)\s*'
    r'(?:independent\s*(?:experiments?|biological|replicates?|animals?|mice|rats?|samples?|preparations?))|'
    r'in\s*(triplicate|quadruplicate|quintuplicate)|'
    r'(three|four|five|3|4|5)\s*(?:mice|rats?|animals?|patients?)\s*(?:per|each|/)'
    r')',
    re.IGNORECASE,
)

# ── Negative filter: skip matches that cite other people's data ──────────────
# If the context window around a sus_claim hits any of these, don't count it.
# Prevents false positives from: "As reported by Smith et al. (n=3, p<0.01)"
NEGATIVE_FILTER = re.compile(
    r'\b(?:previously|recent\s+study|et\s+al|reported\s+by|review|'
    r'meta[- ]analysis|cohorts?\s+from|in\s+literature|'
    r'according\s+to|published\s+by|their\s+study|prior\s+work|'
    r'other\s+groups?|external\s+cohort|publicly\s+available|'
    r'database\b|deposited|GEO\b|TCGA\b|GTEx\b)\b',
    re.IGNORECASE,
)

# ── Experiment type detection ────────────────────────────────────────────────
EXPERIMENT_TYPES = {
    "细胞/分子实验 (in vitro)": re.compile(
        r'\b(?:cells?|HEK293|HeLa|CHO\b|fibroblast|epithelial|'
        r'culture|in\s*vitro|transfection|lysate|'
        r'western\s*blot|immunoblot|qPCR|RT[-\s]?PCR|'
        r'flow\s*cytometry|FACS|ELISA|recombinant|plasmid)\b',
        re.IGNORECASE,
    ),
    "动物实验 (in vivo)": re.compile(
        r'\b(?:mice|mouse|rats?|rabbits?|dogs?|pigs?|swine|'
        r'monkey|primate|animal|in\s*vivo|C57BL|BALB/c|'
        r'SD\s*rat|IACUC|sacrificed|euthan|gavage|i\.p\.|i\.v\.)\b',
        re.IGNORECASE,
    ),
    "临床/人体研究": re.compile(
        r'\b(?:patients?|human|subjects?|clinical|cohort|trial|'
        r'randomi[sz]ed|IRB|informed\s*consent|placebo|'
        r'epidemiology|prospective|retrospective)\b',
        re.IGNORECASE,
    ),
}

# Typical effect size ranges by experiment type (Cohen's d)
TYPICAL_EFFECT = {
    "细胞/分子实验 (in vitro)": (1.5, 3.0),
    "动物实验 (in vivo)": (0.8, 2.0),
    "临床/人体研究": (0.2, 0.5),
}

# Reference effect sizes for p-value table
REFERENCE_EFFECT_SIZES = [
    ("小效应", 0.2),
    ("中小效应", 0.5),
    ("中等效应", 0.8),
    ("大效应", 1.5),
    ("很大效应", 2.0),
    ("极巨大效应", 3.0),
]


def _compute_p_table(n_per_group: int) -> list[dict]:
    """Compute minimum achievable p-values for given n and reference effect sizes.

    Uses the exact t-distribution (two-sample, equal n, two-sided).
    """
    from scipy.stats import t as t_dist

    df = 2 * n_per_group - 2
    if df < 1:
        return []

    rows = []
    for label, d in REFERENCE_EFFECT_SIZES:
        t_stat = d * math.sqrt(n_per_group / 2.0)
        p_val = 2.0 * t_dist.sf(abs(t_stat), df)
        rows.append({
            "label": label,
            "d": d,
            "t": round(t_stat, 2),
            "p": p_val,
            "p_display": _format_p(p_val),
            "sig_05": p_val < 0.05,
            "sig_01": p_val < 0.01,
        })
    return rows


def _format_p(p: float) -> str:
    """Format p-value for display."""
    if p < 0.0001:
        return "p < 0.0001"
    elif p < 0.001:
        return f"p ≈ {p:.4f}"
    elif p < 0.01:
        return f"p ≈ {p:.4f}"
    else:
        return f"p ≈ {p:.3f}"


def _detect_experiment_types(text: str) -> list[str]:
    """Detect which experiment types are mentioned in the text."""
    found = []
    for etype, pattern in EXPERIMENT_TYPES.items():
        if pattern.search(text):
            found.append(etype)
    return found if found else ["未知实验类型"]


def _build_claim_compact(n_per_group: int, exp_types: list[str]) -> list[str]:
    """Compact per-claim p-value analysis: ~5 lines instead of ~15."""
    table = _compute_p_table(n_per_group)
    if not table:
        return []

    lines = []
    df = 2 * n_per_group - 2
    min_d_05 = _min_d_for_significance(n_per_group, 0.05)
    min_d_01 = _min_d_for_significance(n_per_group, 0.01)
    min_d_001 = _min_d_for_significance(n_per_group, 0.001)

    # Which d values reach significance?
    sig_ds = [f"d={r['d']:.1f}→{r['p_display']}" for r in table if r['sig_05']]
    if sig_ds:
        lines.append(f"  可达显著的效应量 (df={df}): {', '.join(sig_ds)}")
    else:
        worst = table[-1]
        lines.append(
            f"  即使 d={worst['d']:.1f}（{worst['label']}）: {worst['p_display']} ← 仍无法显著 (df={df})"
        )

    # Critical d thresholds in one line
    d001_str = f"d≥{min_d_001:.1f}（极不可能）" if min_d_001 > 5.0 else f"d≥{min_d_001:.2f}"
    lines.append(f"  临界 d: p<0.05 需 d≥{min_d_05:.2f} | p<0.01 需 d≥{min_d_01:.2f} | p<0.001 需 {d001_str}")

    # Experiment type context
    for etype in exp_types:
        if etype in TYPICAL_EFFECT:
            lo, hi = TYPICAL_EFFECT[etype]
            lines.append(f"  [{etype}] 典型效应量 d={lo}-{hi}")

    # Plausibility vs typical effect sizes
    if n_per_group <= 5:
        relevant_hi = 1.5  # default conservative
        for et in exp_types:
            if et in TYPICAL_EFFECT:
                relevant_hi = max(relevant_hi, TYPICAL_EFFECT[et][1])
        if min_d_05 > relevant_hi:
            lines.append(
                f"  ⚠️ p<0.05 需 d≥{min_d_05:.2f} > 典型效应量上限 d≈{relevant_hi}——显著声称不合理"
            )
        if min_d_01 > 3.0:
            lines.append(f"  🔴 p<0.01 需 d≥{min_d_01:.2f}，在生物实验中几乎不可能，建议重点审查原始数据")

    return lines


def _min_d_for_significance(n_per_group: int, alpha: float) -> float:
    """Compute minimum Cohen's d needed to reach significance at given alpha.

    d_min = t_crit(alpha, df) × sqrt(2/n)
    """
    from scipy.stats import t as t_dist

    df = 2 * n_per_group - 2
    if df < 1:
        return float("inf")
    t_crit = t_dist.ppf(1 - alpha / 2, df)
    return t_crit * math.sqrt(2.0 / n_per_group)


def run_sample_size_check(paper) -> list[CheckResult]:
    """Check sample size adequacy — per-claim compartmentalised analysis."""
    methods = paper.methods or ""
    full_text = paper.full_text or ""

    if not full_text:
        return [
            CheckResult(
                check_id="stats.sample_size",
                check_name="样本量校验",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    def extract_n(text: str) -> list[int]:
        values: list[int] = []
        seen: set[tuple[int, int]] = set()
        for pattern in N_PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span in seen:
                    continue
                seen.add(span)
                n = int(m.group(1))
                if 1 <= n <= 1000:
                    values.append(n)
        return values

    methods_n = extract_n(methods or full_text)
    full_n = extract_n(full_text)
    has_power = bool(POWER_ANALYSIS.search(methods or full_text))

    all_n = full_n if full_n else (methods_n if methods_n else [])
    methods_only_n = methods_n if methods_n else []

    # ── 1. Extract suspicious claims (n + significance in same context) ───
    sus_claims = []
    seen_spans: set[tuple[int, int]] = set()

    for pattern in [SMALL_N_SIGNIFICANCE, SMALL_N_P_LOOSE]:
        for match in pattern.finditer(full_text):
            span = (match.start(), match.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)

            n_val = None
            for g in match.groups():
                if g is not None:
                    n_val = int(g)
                    break
            if n_val is None or n_val > 4:
                continue  # n=5 and n=6 are normal for most experiments

            # Wider context window for experiment type detection + negative filter
            ctx_start = max(0, match.start() - 80)
            ctx_end = min(len(full_text), match.end() + 120)
            ctx = full_text[ctx_start:ctx_end].replace('\n', ' ').strip()

            # Negative filter: skip if context cites other people's data
            if NEGATIVE_FILTER.search(ctx):
                continue

            # Detect experiment type from LOCAL context (not global)
            local_exp = _detect_experiment_types(ctx)

            sus_claims.append({
                "n": n_val,
                "context": ctx,
                "exp_types": local_exp,
                "min_d_05": _min_d_for_significance(n_val, 0.05),
                "min_d_001": _min_d_for_significance(n_val, 0.001),
            })

    # ── 2. Detect pseudoreplication ───────────────────────────────────────
    tech_rep_hits = list(TECH_REPLICATE.finditer(full_text))
    hidden_n_hits = list(HIDDEN_N.finditer(full_text))

    # ── 3. Detect experiment types (global, for overview) ─────────────────
    global_exp_types = _detect_experiment_types(full_text)

    # ── 4. Build evidence ─────────────────────────────────────────────────
    evidence = []

    # --- Global summary ---
    methods_display = sorted(set(methods_only_n)) if methods_only_n else []
    full_display = sorted(set(all_n)) if all_n else []
    evidence.append(f"[全局] Methods 样本量: {methods_display if methods_display else '未检出'}")
    if all_n:
        evidence.append(f"[全局] 全文样本量范围: {min(all_n)}-{max(all_n)}（共 {len(full_display)} 个不同 n 值）")
    if global_exp_types:
        evidence.append(f"[全局] 检测到实验类型: {', '.join(global_exp_types)}")

    # Methods-Results gap
    methods_results_gap = False
    if methods_only_n and all_n:
        methods_min_n = min(methods_only_n)
        full_min_n = min(all_n)
        if full_min_n < methods_min_n:
            methods_results_gap = True
            evidence.append(
                f"[全局] ⚠️ Methods 声称最小 n={methods_min_n}，"
                f"但 Results/图注中最小 n={full_min_n}——样本量不一致"
            )

    if has_power:
        evidence.append("[全局] ✓ 提及了样本量功效分析")

    # --- Individual claim analyses (the core output) ---
    if sus_claims:
        evidence.append("")
        evidence.append(f"── 逐条可疑声明分析（共 {len(sus_claims)} 条）──")

        for i, claim in enumerate(sus_claims):
            n_val = claim["n"]
            claim_label = f"可疑声明 #{i + 1}"
            evidence.append(f"")
            evidence.append(f"  {claim_label} | n={n_val} | 实验类型: {', '.join(claim['exp_types'])}")
            evidence.append(f"  上下文: \"{claim['context'][:150]}\"")

            if n_val < 3:
                evidence.append(
                    f"  ⚠️ n={n_val}：方差估计自由度={2 * n_val - 2}，"
                    f"无法进行有意义的方差估计和统计检验。"
                    f"不存在效应量 d 能拯救 n={n_val} 的实验设计。"
                )
            elif n_val == 3:
                evidence.append(
                    f"  ⚠️ n=3 时方差估计极不可靠（每组 df=1），"
                    f"以下为理想假设下的理论下限："
                )
                evidence.extend(_build_claim_compact(n_val, claim["exp_types"]))
            else:
                evidence.extend(_build_claim_compact(n_val, claim["exp_types"]))


    # --- Pseudoreplication warnings ---
    if tech_rep_hits:
        evidence.append("")
        evidence.append(f"── ⚠️ 伪重复 (Pseudoreplication) 警告（共 {len(tech_rep_hits)} 处）──")
        for i, m in enumerate(tech_rep_hits[:3]):
            start = max(0, m.start() - 30)
            end = min(len(full_text), m.end() + 50)
            ctx = full_text[start:end].replace('\n', ' ').strip()
            evidence.append(f"  #{i + 1}: \"...{ctx}...\"")
        evidence.append(
            f"  → 技术重复（同一样本分 3 孔/3 次测量）不能作为生物学重复进行组间统计推断。"
            f"若论文将 n=3 个复孔当作 n=3 个独立样本来跑 t 检验，属于伪重复——"
            f"这不是功效低的问题，是检验根本不成立。"
        )

    if hidden_n_hits and not full_n:
        evidence.append("")
        evidence.append(
            f"  💡 检测到 {len(hidden_n_hits)} 处非标准样本量表述"
            f"（如 'in triplicate'、'three independent experiments'），"
            f"但未匹配到标准 n=X 格式。可能遗漏了样本量信息。"
        )

    # ── 5. Global decision ─────────────────────────────────────────────────
    methods_min = min(methods_only_n) if methods_only_n else None
    full_min = min(all_n) if all_n else None

    # Count sus_claims by severity
    n_claims_lt3 = sum(1 for c in sus_claims if c["n"] < 3)
    n_claims_eq3 = sum(1 for c in sus_claims if c["n"] == 3)
    worst_min_d = min((c["min_d_05"] for c in sus_claims), default=99)

    if methods_min is None:
        # ── Fallback: no Methods section extracted ─────────────────────────
        evidence.append("[全局] ⚠️ 未提取到 Methods 章节，无法确认方法论样本量声明")

        if n_claims_lt3 >= 2:
            level = "orange"
            verdict = (
                f"检测到 {n_claims_lt3} 处 n<3 的可疑显著声称，"
                f"但因缺少 Methods 章节无法交叉验证。建议人工审查全文。"
            )
        elif n_claims_lt3 == 1:
            level = "yellow"
            verdict = (
                f"检测到 1 处 n<3 的可疑显著声称，但因缺少 Methods 章节无法交叉验证。"
            )
        elif sus_claims:
            level = "yellow"
            verdict = f"检测到 {len(sus_claims)} 处小样本+显著声称，但因缺少 Methods 章节无法交叉验证。"
        else:
            level = "green"
            verdict = "未检测到显式样本量声明（可能使用了其他描述方式）。"
            if not methods_only_n:
                verdict += " 注意：未提取到 Methods 章节。"

    elif methods_min < 3:
        # ── RED: Methods ITSELF declares n < 3 ─────────────────────────────
        level = "red"
        verdict = (
            f"Methods 声明样本量 n={methods_min}，n < 3 无法计算方差，"
            f"统计检验不成立。"
        )
        if not has_power:
            verdict += " 且未提及样本量功效分析。"

    elif n_claims_lt3 >= 2:
        # ── RED: multiple sus_claims with n < 3 ────────────────────────────
        level = "red"
        verdict = (
            f"{len(sus_claims)} 处小样本+显著声称中有 {n_claims_lt3} 处 n<3，"
            f"Methods 声明最小 n={methods_min}——Methods 与声称严重不一致。"
        )

    elif n_claims_lt3 == 1:
        # ── ORANGE: one sus_claim with n < 3 ───────────────────────────────
        level = "orange"
        claim_n = next(c["n"] for c in sus_claims if c["n"] < 3)
        verdict = (
            f"1 处可疑声称 n={claim_n} 但 Methods 声明 n≥{methods_min}，"
            f"可能存在样本量报告不一致。"
        )

    elif sus_claims:
        # ── Claims with n ≥ 3: judge by effect size plausibility ───────────
        worst_n = min(c["n"] for c in sus_claims)
        if worst_n == 3 or worst_min_d > 2.0:
            level = "yellow"
            verdict = (
                f"{len(sus_claims)} 处小样本+显著声称，"
                f"最小 n={worst_n}，达到 p<0.05 需要 d≥{worst_min_d:.1f}。"
            )
        else:
            level = "green"
            verdict = (
                f"{len(sus_claims)} 处小样本+显著声称，"
                f"但临界效应量 d≥{worst_min_d:.2f} 在合理范围内。"
            )
        if not has_power:
            verdict += " 建议提供样本量计算依据。"

    elif methods_results_gap and methods_min and methods_min >= 3 and full_min and full_min < methods_min:
        # ── YELLOW: Methods vs Results gap (downgraded from orange) ────────
        level = "yellow"
        verdict = (
            f"Methods 声称最小 n={methods_min}，"
            f"但 Results/图注中最小 n={full_min}——可能是 1-2 例样本脱落，"
            f"而非系统性少报。建议核实。"
        )

    elif all_n:
        # ── GREEN: all good ────────────────────────────────────────────────
        level = "green"
        verdict = f"样本量范围 {min(all_n)}-{max(all_n)}"
        if has_power:
            verdict += "，且提及了功效分析"
        # Note stray n=2 but don't flag red
        if full_min is not None and full_min < 3 and methods_min and methods_min >= 3:
            verdict += (
                f"（全文检测到 n={full_min}，但 Methods 声明 n≥{methods_min}——"
                f"该 n={full_min} 可能来自描述性统计或引述他人数据，已排除）"
            )

    else:
        level = "green"
        verdict = "未检测到显式样本量声明（可能使用了其他描述方式）"

    # ── Pseudoreplication: separate signal, appended independently ─────────
    if tech_rep_hits:
        verdict += (
            f" | ⚠️ 另检测到 {len(tech_rep_hits)} 处疑似技术重复/伪重复"
            f"（技术与生物学重复混淆——独立信号，与样本量判定无关）"
        )

    return [
        CheckResult(
            check_id="stats.sample_size",
            check_name="样本量校验",
            level=level,
            verdict=verdict,
            evidence=evidence[:50],
            confidence=0.85,
            needs_human=level != "green",
            human_instruction=(
                "逐条审查上述「可疑声明」中的 n 值和 p 值。\n\n"
                "关键审查点：\n"
                "1. n < 3：无法计算方差，任何统计检验不成立。\n"
                "2. n=3：t-test/Mann-Whitney 功效极低。用非参数检验并提供效应量。\n"
                "3. 区分生物学重复 vs 技术重复：同一块组织切 3 片、同一样本加 3 个孔、"
                "同一份 RNA 跑 3 次 qPCR——这些都是技术重复，不能用于组间统计推断。"
                "若论文将技术重复当作生物学重复来算 p 值，属于伪重复 (pseudoreplication)。\n"
                "4. Methods 声明的 n vs 图注中的 n 不一致：可能存在选择性报告。\n"
                "5. 实验类型影响判断：细胞实验 n=3 且 p<0.05 勉强可能；"
                "动物实验 n=3 且 p<0.01 极可疑；临床研究小 n 不具统计意义。\n"
                "6. 小样本 + p < 0.001：在绝大多数生物实验中需要审查原始数据。"
            ),
        )
    ]
