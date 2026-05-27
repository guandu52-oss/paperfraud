"""Arithmetic relationship detection between groups.

Tests whether Group B values are a constant multiple of Group A values
across multiple rows (e.g., Group_B = Group_A × 1.5 for all rows with
n ≥ 10).

If the CV of Group_B / Group_A is < 1%, values are likely formula-generated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from paperfraud.base import CheckResult, SourceLocation


@dataclass
class ColumnData:
    header: str
    values: list[float]
    n: int


def _is_valid_header(header: str) -> bool:
    """Reject garbage column names from PDF text extraction artifacts."""
    if len(header) < 3:
        return False
    # Too many newlines → multi-column spillover from PDF
    if header.count("\n") > 2:
        return False
    # Unreasonably long → concatenated table headers
    if len(header) > 80:
        return False
    # Must contain at least one alphabetic character
    if not re.search(r"[A-Za-z]", header):
        return False
    # Repeated segments → merged cells from adjacent columns
    parts = [p.strip() for p in header.split("\n") if p.strip()]
    if len(parts) >= 3 and len(set(parts)) < len(parts):
        return False
    return True


def extract_table_columns(text: str) -> list[ColumnData]:
    """Extract numeric columns from table-like text structures.

    Looks for patterns like:
      Control: 1.23, 2.34, 3.45
      Treatment: 1.85, 3.51, 5.18
    Or tabular data with headers followed by aligned numbers.
    """
    columns: list[ColumnData] = []

    # Find lines that look like column headers followed by data
    # Pattern: "Group Name  1.23 ± 0.45  2.34 ± 0.56  3.45 ± 0.67"
    table_pattern = re.compile(
        r'(?P<header>[A-Za-z][A-Za-z\s\-+()]+?)'
        r'(?P<numbers>(?:\s*\d+\.?\d*\s*(?:[±±,;]\s*\d+\.?\d*)?)+)',
        re.IGNORECASE,
    )

    # Also match tab/space-separated numeric rows
    for match in table_pattern.finditer(text):
        header = match.group("header").strip()
        num_str = match.group("numbers")

        # Filter garbage column names from PDF extraction artifacts
        if not _is_valid_header(header):
            continue

        # Extract all numbers from the data part
        nums = re.findall(r'(\d+\.?\d*)', num_str)
        try:
            values = [float(n) for n in nums]
        except (ValueError, TypeError):
            continue

        if len(values) >= 3:
            columns.append(ColumnData(header=header, values=values, n=len(values)))

    return columns


def check_arithmetic_relationship(col_a: ColumnData, col_b: ColumnData, threshold_cv: float = 0.01) -> dict:
    """Check if col_b = col_a × constant across all rows.

    Args:
        col_a: Reference column
        col_b: Comparison column
        threshold_cv: CV threshold below which relationship is suspicious

    Returns dict with ratio, CV, is_suspicious, details
    """
    if col_a.n != col_b.n:
        # Try to align by length
        min_len = min(col_a.n, col_b.n)
        vals_a = col_a.values[:min_len]
        vals_b = col_b.values[:min_len]
    else:
        vals_a = col_a.values
        vals_b = col_b.values

    if len(vals_a) < 3:
        return {"error": "不足 3 对数值，无法检验"}

    arr_a = np.array(vals_a, dtype=float)
    arr_b = np.array(vals_b, dtype=float)

    # Avoid division by zero
    mask = arr_a != 0
    if mask.sum() < 3:
        return {"error": "A 列含零过多，无法计算比值"}

    ratios = arr_b[mask] / arr_a[mask]

    mean_ratio = float(np.mean(ratios))
    std_ratio = float(np.std(ratios, ddof=1))
    cv = std_ratio / abs(mean_ratio) if mean_ratio != 0 else float('inf')

    return {
        "mean_ratio": mean_ratio,
        "std_ratio": std_ratio,
        "cv": cv,
        "is_suspicious": cv < threshold_cv and len(vals_a) >= 5,
        "n_pairs": len(ratios),
        "ratio_values": [round(r, 4) for r in ratios],
    }


def run_arithmetic_check(paper) -> list[CheckResult]:
    """Check all pairs of numeric columns for exact constant ratio relationships."""
    MAX_TEXT = 500_000
    text = paper.results or paper.full_text
    text_truncated = len(text) > MAX_TEXT
    if text_truncated:
        text = text[:MAX_TEXT]
    if not text:
        return [
            CheckResult(
                check_id="numbers.arithmetic",
                check_name="组间算术关系检测",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    try:
        columns = extract_table_columns(text)
    except Exception:
        return [
            CheckResult(
                check_id="numbers.arithmetic",
                check_name="组间算术关系检测",
                level="yellow",
                verdict="表格解析失败，跳过检查（表格含复杂跨行/跨列结构）",
                needs_human=False,
            )
        ]

    if len(columns) < 2:
        result = CheckResult(
            check_id="numbers.arithmetic",
            check_name="组间算术关系检测",
            level="green",
            verdict=f"仅提取到 {len(columns)} 列数值，无法进行组间比较",
            needs_human=False,
        )
        if text_truncated:
            result.verdict += "（文本已截断）"
        return [result]

    suspicious: list[dict] = []
    evidence: list[str] = []
    if text_truncated:
        evidence.append("⚠️ 输入文本超过 500K 字符，仅检测前 500K")

    for col_a, col_b in combinations(columns, 2):
        try:
            result = check_arithmetic_relationship(col_a, col_b)
        except Exception:
            continue
        if result.get("is_suspicious"):
            suspicious.append({
                "col_a": col_a.header,
                "col_b": col_b.header,
                "result": result,
            })
            evidence.append(
                f'"{col_a.header}" vs "{col_b.header}": '
                f"比值均值={result['mean_ratio']:.4f}, "
                f"CV={result['cv']:.4%}, "
                f"N={result['n_pairs']}"
            )

    if suspicious:
        return [
            CheckResult(
                check_id="numbers.arithmetic",
                check_name="组间算术关系检测",
                level="orange",
                verdict=f"{len(suspicious)} 对列存在精确常数倍关系（CV < 1%）" + ("（文本已截断）" if text_truncated else ""),
                evidence=evidence,
                confidence=0.8,
                needs_human=True,
                human_instruction="精确常数倍关系暗示数据可能是公式生成的（如 Group B = Group A × 1.5）。"
                "验证原文中这些组是否为独立测量。",
            )
        ]

    return [
        CheckResult(
            check_id="numbers.arithmetic",
            check_name="组间算术关系检测",
            level="green",
            verdict=f"已比较 {len(columns)} 列，{len(columns)*(len(columns)-1)//2} 对，未发现精确常数倍关系" + ("（文本已截断）" if text_truncated else ""),
            evidence=evidence,
            confidence=0.75,
            needs_human=False,
        )
    ]
