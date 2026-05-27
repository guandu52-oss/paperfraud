"""Pure Python reimplementation of statcheck.

Extracts APA-formatted statistical results from text and recomputes
P-values using scipy.stats. Flags inconsistencies between reported and
recomputed P-values.

APA format patterns:
  t(df) = value, p = ...
  F(df1, df2) = value, p = ...
  χ²(df, N = n) = value, p = ...
  chi²(df, N = n) = value, p = ...
  r(df) = value, p = ...
  Z = value, p = ...
"""

from __future__ import annotations

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from scipy import stats as scipy_stats

from paperfraud.base import CheckResult, SourceLocation


@dataclass
class StatResult:
    """A single extracted statistical result."""
    test_type: str          # "t", "F", "chisq", "r", "Z"
    df1: float | None = None
    df2: float | None = None
    n: float | None = None
    reported_value: float | None = None
    reported_p: float | None = None
    reported_p_text: str = ""       # "p < 0.05", "p = 0.032", "p > 0.05"
    p_comparison: str = "="          # "<", "=", ">"
    recalculated_p: float | None = None
    raw_match: str = ""
    is_error: bool = False
    error_type: str = ""             # "decision_error" | "gross_mismatch" | ""


# APA statistical result patterns — each with (test_type, pattern)
# Ordered by length/specificity: F before t to avoid F(1,28) matching as t(28)
# Chisq before t to avoid t matching inside "chi-squared"
APA_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("F", re.compile(
        r'\bF\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*,\s*(?P<df2>\d+(?:\.\d+)?)\s*\)'
        r'\s*=\s*(?P<value>\d+\.?\d*)'
        r'(?:[,\s;]+(?P<p_text>p\s*[<>=]\s*\.?\d*\.?\d*|p\s*=\s*\.?\d*\.?\d*))?',
        re.IGNORECASE,
    )),
    ("chisq", re.compile(
        r'(?:χ\s*[²2]|chi\s*(?:square|sq|squared|²|2)?)\s*'
        r'\(\s*(?P<df1>\d+(?:\.\d+)?)'
        r'(?:\s*,\s*(?:N|n)\s*=\s*(?P<n>\d+(?:\.\d+)?))?\s*\)'
        r'\s*=\s*(?P<value>\d+\.?\d*)'
        r'(?:[,\s;]+(?P<p_text>p\s*[<>=]\s*\.?\d*\.?\d*|p\s*=\s*\.?\d*\.?\d*))?',
        re.IGNORECASE,
    )),
    ("t", re.compile(
        r'\bt\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*\)'
        r'\s*=\s*(?P<value>\d+\.?\d*)'
        r'(?:[,\s;]+(?P<p_text>p\s*[<>=]\s*\.?\d*\.?\d*|p\s*=\s*\.?\d*\.?\d*))?',
        re.IGNORECASE,
    )),
    ("r", re.compile(
        r'\br\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*\)'
        r'\s*=\s*(?P<value>-?\d*\.?\d*)'
        r'(?:[,\s;]+(?P<p_text>p\s*[<>=]\s*\.?\d*\.?\d*|p\s*=\s*\.?\d*\.?\d*))?',
        re.IGNORECASE,
    )),
    ("Z", re.compile(
        r'\b[Zz]\s*=\s*(?P<value>\d+\.?\d*)'
        r'(?:[,\s;]+(?P<p_text>p\s*[<>=]\s*\.?\d*\.?\d*|p\s*=\s*\.?\d*\.?\d*))?',
        re.IGNORECASE,
    )),
]

# P-value extraction: "p = 0.032", "p < 0.05", "p > 0.05"
P_VALUE_RE = re.compile(
    r'p\s*(?P<comp>[<>=])\s*(?P<p_val>\.?\d+\.?\d*)',
    re.IGNORECASE,
)

# "ns" or "n.s." = not significant
NS_RE = re.compile(r'\b(?:ns|n\.s\.)\b', re.IGNORECASE)


def extract_stats(text: str, page: int = 1) -> list[StatResult]:
    """Extract all APA-formatted statistical results from text."""
    results: list[StatResult] = []

    for test_type, pattern in APA_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()

            result = StatResult(
                test_type=test_type,
                df1=_parse_float(groups.get("df1")),
                df2=_parse_float(groups.get("df2")),
                n=_parse_float(groups.get("n")),
                reported_value=_parse_float(groups.get("value")),
                raw_match=match.group(0).strip(),
            )

            # Parse reported p-value
            p_text = groups.get("p_text", "")
            if p_text:
                p_match = P_VALUE_RE.search(p_text)
                if p_match:
                    result.p_comparison = p_match.group("comp")
                    result.reported_p = _parse_float(p_match.group("p_val"))
                    result.reported_p_text = p_text.strip()

            results.append(result)

    return results


def recalculate_p(result: StatResult) -> StatResult:
    """Recalculate P-value from test statistic and df using scipy.stats."""
    value = result.reported_value
    if value is None:
        return result

    try:
        if result.test_type == "t" and result.df1 is not None:
            # Two-tailed t-test
            result.recalculated_p = scipy_stats.t.sf(abs(value), df=result.df1) * 2

        elif result.test_type == "F" and result.df1 is not None and result.df2 is not None:
            result.recalculated_p = scipy_stats.f.sf(value, dfn=result.df1, dfd=result.df2)

        elif result.test_type == "chisq" and result.df1 is not None:
            result.recalculated_p = scipy_stats.chi2.sf(value, df=result.df1)

        elif result.test_type == "r" and result.df1 is not None:
            # r → t = r * sqrt(df / (1 - r²)), then two-tailed t-test
            r_val = value
            if abs(r_val) >= 1.0:
                return result
            t_val = r_val * math.sqrt(result.df1 / (1 - r_val * r_val))
            result.recalculated_p = scipy_stats.t.sf(abs(t_val), df=result.df1) * 2

        elif result.test_type == "Z":
            # Two-tailed Z-test
            result.recalculated_p = scipy_stats.norm.sf(abs(value)) * 2

    except (ValueError, ZeroDivisionError):
        pass

    return result


def flag_inconsistency(result: StatResult, alpha: float = 0.05) -> StatResult:
    """Compare reported vs recalculated P-value and flag discrepancies."""
    if result.recalculated_p is None or result.reported_p is None:
        return result

    recalc = result.recalculated_p
    reported = result.reported_p
    comp = result.p_comparison

    # Decision error: reported significant but recalculated is not (or vice versa)
    reported_sig = (comp == "<" and reported <= alpha) or (comp == "=" and reported < alpha)
    recalc_sig = recalc < alpha

    if reported_sig != recalc_sig:
        result.is_error = True
        result.error_type = "decision_error"

    # Gross mismatch: both significant/non-significant, but values wildly different
    if not result.is_error:
        if reported > 0 and recalc > 0:
            ratio = max(reported, recalc) / (min(reported, recalc) + 1e-10)
            if ratio > 100:
                result.is_error = True
                result.error_type = "gross_mismatch"

    return result


def run_py_statcheck(paper) -> list[CheckResult]:
    """Run pure-Python statcheck on a ParsedPaper.

    Extracts stats from Results section (fallback: full text), recalculates
    P-values, and flags decision errors.
    """
    MAX_TEXT = 500_000
    text = paper.results or paper.full_text
    text_truncated = len(text) > MAX_TEXT
    if text_truncated:
        text = text[:MAX_TEXT]
    if not text:
        return [
            CheckResult(
                check_id="numbers.statcheck",
                check_name="P 值反算 (py-statcheck)",
                level="error",
                verdict="无法执行：论文全文/Results 部分未提取到文本",
                evidence=[],
                needs_human=False,
            )
        ]

    stats = extract_stats(text)
    if not stats:
        result = CheckResult(
            check_id="numbers.statcheck",
            check_name="P 值反算 (py-statcheck)",
            level="green",
            verdict="未检测到 APA 格式统计量，或论文不包含传统假设检验",
            evidence=[],
            needs_human=False,
        )
        if text_truncated:
            result.verdict += "（注意：文本过长已截断，可能遗漏尾部统计量）"
            result.evidence.append("输入文本超过 500K 字符，仅检测前 500K")
        return [result]

    # Recalculate and flag
    for stat in stats:
        recalculate_p(stat)
        flag_inconsistency(stat)

    errors = [s for s in stats if s.is_error]
    total = len(stats)

    evidence = []
    if text_truncated:
        evidence.append("⚠️ 输入文本超过 500K 字符，仅检测前 500K，可能遗漏尾部统计量")
    locations = []
    for s in errors:
        evidence.append(
            f"[{s.test_type}] {s.raw_match} → "
            f"报告 P{s.p_comparison}{s.reported_p}, 反算 P={s.recalculated_p:.4f} ({s.error_type})"
        )
        locations.append(SourceLocation(page=1, excerpt=s.raw_match))

    if errors:
        return [
            CheckResult(
                check_id="numbers.statcheck",
                check_name="P 值反算 (py-statcheck)",
                level="red",
                verdict=f"{len(errors)}/{total} 个统计量 P 值反算不一致" + ("（文本已截断）" if text_truncated else ""),
                evidence=evidence,
                source_locations=locations,
                confidence=0.9,
                needs_human=True,
                human_instruction="检查不一致统计量的上下文：是否报告了精确 P 值还是不等式？是否为单尾检验？",
            )
        ]

    return [
        CheckResult(
            check_id="numbers.statcheck",
            check_name="P 值反算 (py-statcheck)",
            level="green",
            verdict=f"全部 {total} 个 APA 格式统计量反算一致" + ("（文本已截断）" if text_truncated else ""),
            evidence=evidence,
            confidence=0.85,
            needs_human=False,
        )
    ]


def _parse_float(val: str | None) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
