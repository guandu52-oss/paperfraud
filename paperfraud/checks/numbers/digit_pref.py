"""Digit preference analysis and decimal consistency check.

Digit Preference: The last digits of reported values should follow
a uniform distribution (0-9 each ~10%). If 0 and 5 combined exceed 35%,
it suggests human fabrication (people round to 0 and 5).

Decimal Consistency: All values in the same column/context should have
the same number of decimal places. Mixed precision (e.g., 2.3 and 2.345
in the same column) is a red flag.
"""

from __future__ import annotations

import re
from collections import Counter

from scipy import stats as scipy_stats

from paperfraud.base import CheckResult, SourceLocation


def extract_numeric_values(text: str) -> list[float]:
    """Extract all decimal numbers from text (excluding years, P-values, references)."""
    # Match numbers with decimal points, but not years (4-digit) or standalone integers
    pattern = re.compile(r'(?<!\d)(?:\d+\.\d+)(?!\d)')
    numbers = []

    for match in pattern.finditer(text):
        val_str = match.group(0)
        # Skip years (1900-2099)
        if re.match(r'^(19|20)\d{2}$', val_str):
            continue
        # Skip numbers that look like section labels (1.1, 2.3.4)
        if re.match(r'^\d+\.\d+(?:\.\d+)+$', val_str):
            continue
        try:
            numbers.append(float(val_str))
        except ValueError:
            pass

    return numbers


def check_digit_preference(values: list[float]) -> dict:
    """Analyze last digit distribution.

    Returns:
      p_value: chi-squared p-value for uniformity
      digit_counts: Counter of last digits 0-9
      zero_five_pct: combined percentage of 0 and 5
      is_suspicious: bool
    """
    last_digits = []
    for v in values:
        s = f"{v:.10f}".rstrip('0').rstrip('.')
        # Get the last significant digit
        if '.' in s:
            fractional = s.split('.')[1]
            if fractional:
                last_digits.append(int(fractional[-1]))
                continue
        # Fallback: last digit of integer representation
        int_part = str(abs(int(v)))
        if int_part:
            last_digits.append(int(int_part[-1]))

    if len(last_digits) < 20:
        return {
            "p_value": None,
            "digit_counts": Counter(last_digits),
            "zero_five_pct": None,
            "is_suspicious": False,
            "note": "样本量不足 (< 20 个数)，检验效力不够",
        }

    observed = Counter(last_digits)
    n_total = len(last_digits)
    zero_five_pct = (observed.get(0, 0) + observed.get(5, 0)) / n_total * 100

    # Chi-squared test against uniform distribution
    expected = [n_total / 10] * 10
    observed_list = [observed.get(d, 0) for d in range(10)]

    # Suppress divide-by-zero warnings for low counts
    chi2, p_value = scipy_stats.chisquare(observed_list, f_exp=expected)

    return {
        "p_value": p_value,
        "digit_counts": observed,
        "zero_five_pct": zero_five_pct,
        "is_suspicious": p_value < 0.05 or zero_five_pct > 35,
    }


def check_decimal_consistency(values: list[float]) -> dict:
    """Check if all values have consistent decimal places.

    Returns:
      consistent: bool
      decimal_places: Counter of decimal place counts
      most_common_places: int
    """
    decimal_counts = Counter()
    for v in values:
        s = f"{v:.10f}".rstrip('0').rstrip('.')
        if '.' in s:
            decimal_counts[len(s.split('.')[1])] += 1
        else:
            decimal_counts[0] += 1

    if not decimal_counts:
        return {"consistent": True, "decimal_places": decimal_counts, "most_common_places": 0}

    most_common = decimal_counts.most_common(1)[0][1]
    total = sum(decimal_counts.values())
    uniformity = most_common / total

    return {
        "consistent": uniformity >= 0.8,  # 80%+ share the same precision
        "decimal_places": decimal_counts,
        "most_common_places": decimal_counts.most_common(1)[0][0],
        "uniformity": uniformity,
    }


def run_digit_checks(paper) -> list[CheckResult]:
    """Run digit preference and decimal consistency checks."""
    MAX_TEXT = 500_000
    text = paper.results or paper.full_text
    text_truncated = len(text) > MAX_TEXT
    if text_truncated:
        text = text[:MAX_TEXT]
    if not text:
        return [
            CheckResult(
                check_id="numbers.digit_pref",
                check_name="尾数偏好与小数位一致性",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    values = extract_numeric_values(text)
    if len(values) < 10:
        result = CheckResult(
            check_id="numbers.digit_pref",
            check_name="尾数偏好与小数位一致性",
            level="error",
            verdict=f"数值样本不足（仅提取到 {len(values)} 个小数，需 ≥ 10）",
            needs_human=False,
        )
        if text_truncated:
            result.verdict += "（文本已截断）"
        return [result]

    results = []
    trunc_note = "（文本已截断）" if text_truncated else ""

    # Digit preference
    dp = check_digit_preference(values)
    dp_level = "green"
    dp_verdict = ""
    dp_evidence = []
    if text_truncated:
        dp_evidence.append("⚠️ 输入文本超过 500K 字符，仅检测前 500K")

    if dp["is_suspicious"] and dp["p_value"] is not None:
        dp_level = "orange"
        dp_verdict = (
            f"尾数分布异常（0+5 占比 {dp['zero_five_pct']:.1f}%，"
            f"均匀性 χ² P={dp['p_value']:.4f}）{trunc_note}"
        )
        dp_evidence.append(
            f"0+5 占比 {dp['zero_five_pct']:.1f}%（阈值 35%）"
        )
        dp_evidence.append(
            f"尾数分布 χ² 检验 P={dp['p_value']:.4f}（阈值 0.05）"
        )
    elif dp["p_value"] is None:
        dp_level = "green"
        dp_verdict = dp.get("note", "样本量不足") + trunc_note
    else:
        dp_level = "green"
        dp_verdict = f"尾数分布正常（0+5 占比 {dp['zero_five_pct']:.1f}%，χ² P={dp['p_value']:.3f}）{trunc_note}"

    results.append(
        CheckResult(
            check_id="numbers.digit_pref",
            check_name="尾数偏好",
            level=dp_level,
            verdict=dp_verdict,
            evidence=dp_evidence,
            confidence=0.85 if dp["p_value"] is not None else 0.5,
            needs_human=dp["is_suspicious"],
            human_instruction="检查 0 和 5 偏好是否集中在特定表格/段落中。孤立上下文检验。",
        )
    )

    # Decimal consistency
    dc = check_decimal_consistency(values)
    dc_level = "yellow" if not dc["consistent"] else "green"
    dc_verdict = (
        f"小数位数{'一致' if dc['consistent'] else '不一致'}"
        f"（{dc['most_common_places']} 位占 {dc.get('uniformity', 0):.0%}）{trunc_note}"
    )

    dc_evidence = []
    if not dc["consistent"]:
        dc_evidence.append(
            f"同一上下文中出现多种小数位格式：{dict(dc['decimal_places'])}"
        )

    results.append(
        CheckResult(
            check_id="numbers.decimal_cons",
            check_name="小数位一致性",
            level=dc_level,
            verdict=dc_verdict,
            evidence=dc_evidence,
            confidence=0.7,
            needs_human=not dc["consistent"],
            human_instruction="核实不同小数位的数值是否来自不同表格/上下文。同一列中混合精度是红灯。",
        )
    )

    return results
