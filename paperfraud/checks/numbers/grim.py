"""GRIM (Granularity-Related Inconsistency of Means) test.

For a reported mean M calculated from N integer measurements,
M × N must be very close to an integer. If not, the reported
mean (or N) is inconsistent.

Example: N=12, M=3.47 → N×M = 41.64, not an integer → inconsistent.
         N=12, M=3.5 → N×M = 42.00 → consistent.

Tolerance: 0.01 × N (scales with sample size)
"""

from __future__ import annotations

from __future__ import annotations

import math
import re

from paperfraud.base import CheckResult, SourceLocation


def grim_test(mean: float, n: int, tolerance: float | None = None) -> dict:
    """Run GRIM test on a single mean-N pair.

    Returns dict with:
      consistent: bool
      product: float
      nearest_int: int
      error: float
    """
    if tolerance is None:
        tolerance = max(0.01 * n, 0.01)

    product = mean * n
    nearest_int = round(product)
    error = abs(product - nearest_int)

    return {
        "consistent": error <= tolerance,
        "product": product,
        "nearest_int": nearest_int,
        "error": error,
        "tolerance": tolerance,
    }


def extract_mean_n_pairs(text: str, page: int = 1) -> list[dict]:
    """Extract (mean, N, SD/SEM) tuples from text.

    Pattern: "M = X.XX, SD = Y.YY, N = Z" or "X.XX ± Y.YY (n = Z)"
    """
    results: list[dict] = []

    # Pattern 1: "mean = X.XX (SD = Y.YY, N = Z)" or "M ± SD (n = Z)"
    pattern1 = re.compile(
        r'(?:M(?:ean)?\s*[=:]\s*)?(?P<mean>\d+\.\d+)'
        r'\s*(?:±|±|\\pm|plus/minus|±)\s*'
        r'(?P<sd>\d+\.\d+)'
        r'(?:\s*\((?:\s*(?:SD|SEM?)\s*[=:]\s*)?.*?)?'
        r'.*?'
        r'(?:[nN]\s*[=:]\s*)(?P<n>\d+)',
        re.IGNORECASE,
    )

    # Pattern 2: "N = X, M = Y.YY" or "n = X, mean = Y.YY"
    pattern2 = re.compile(
        r'[nN]\s*[=:]\s*(?P<n>\d+)'
        r'.*?'
        r'(?:M(?:ean)?|average)\s*[=:]\s*(?P<mean>\d+\.\d+)',
        re.IGNORECASE,
    )

    for match in pattern1.finditer(text):
        results.append({
            "mean": float(match.group("mean")),
            "n": int(match.group("n")),
            "sd": float(match.group("sd")),
            "raw": match.group(0).strip(),
        })

    for match in pattern2.finditer(text):
        d = {
            "mean": float(match.group("mean")),
            "n": int(match.group("n")),
            "sd": None,
            "raw": match.group(0).strip(),
        }
        if d not in results:
            results.append(d)

    return results


def run_grim(paper) -> list[CheckResult]:
    """Run GRIM test on all extracted mean-N pairs."""
    MAX_TEXT = 500_000
    text = paper.results or paper.full_text
    text_truncated = len(text) > MAX_TEXT
    if text_truncated:
        text = text[:MAX_TEXT]
    if not text:
        return [
            CheckResult(
                check_id="numbers.grim",
                check_name="GRIM 检验",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    pairs = extract_mean_n_pairs(text)
    if not pairs:
        result = CheckResult(
            check_id="numbers.grim",
            check_name="GRIM 检验",
            level="green",
            verdict="未检测到 M ± SD (N = X) 格式数据，跳过 GRIM",
            needs_human=False,
        )
        if text_truncated:
            result.verdict += "（文本已截断，可能遗漏尾部数据）"
        return [result]

    inconsistent = []
    evidence = []
    if text_truncated:
        evidence.append("⚠️ 输入文本超过 500K 字符，仅检测前 500K")

    for p in pairs:
        result = grim_test(p["mean"], p["n"])
        if not result["consistent"]:
            inconsistent.append(p)
            evidence.append(
                f"M={p['mean']}, N={p['n']} → "
                f"N×M={result['product']:.3f}, "
                f"最近整数={result['nearest_int']}, "
                f"误差={result['error']:.3f} > 容忍度={result['tolerance']:.3f}"
            )

    if inconsistent:
        return [
            CheckResult(
                check_id="numbers.grim",
                check_name="GRIM 检验",
                level="red",
                verdict=f"{len(inconsistent)}/{len(pairs)} 个均值-N 对 GRIM 不一致" + ("（文本已截断）" if text_truncated else ""),
                evidence=evidence,
                source_locations=[SourceLocation(page=1, excerpt=p["raw"]) for p in inconsistent],
                confidence=0.95,
                needs_human=True,
                human_instruction="核实原文中 N 数和均值是否匹配。GRIM 不一致强烈暗示均值或 N 数为编造。",
            )
        ]

    return [
        CheckResult(
            check_id="numbers.grim",
            check_name="GRIM 检验",
            level="green",
            verdict=f"全部 {len(pairs)} 个均值-N 对 GRIM 一致" + ("（文本已截断）" if text_truncated else ""),
            evidence=evidence,
            confidence=0.9,
            needs_human=False,
        )
    ]
