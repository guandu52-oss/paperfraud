"""Identical value detection across different experimental groups.

A common fabrication pattern: copying numeric values between supposedly
independent groups/analytes. Extracts numbers from Results text, clusters
them by nearby context, and flags repeated values with high precision.
"""

from __future__ import annotations

import re
from collections import defaultdict

from paperfraud.base import CheckResult, SourceLocation


def _norm(v: float) -> str:
    """Normalize a float to its full string representation for exact matching."""
    return f"{v:.10f}".rstrip('0').rstrip('.')


def extract_values_with_context(text: str) -> list[dict]:
    """Extract numeric values with surrounding context window.

    Returns list of {value, context, start_pos} dicts.
    """
    pattern = re.compile(r'(?<!\d)(\d+\.\d+)(?!\d)')
    results = []
    for match in pattern.finditer(text):
        val_str = match.group(0)
        if re.match(r'^(19|20)\d{2}$', val_str):
            continue
        if re.match(r'^\d+\.\d+(?:\.\d+)+$', val_str):
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        context = text[start:end].replace('\n', ' ').strip()
        results.append({
            "value": val,
            "value_str": val_str,
            "context": context,
            "start_pos": match.start(),
        })
    return results


# P-value thresholds and common statistical cutoffs — these appear everywhere
_PVALUE_THRESHOLDS = {
    0.05, 0.01, 0.001, 0.0001,
    0.5, 1.0, 1.96,
}
# Numbers that are clearly P-value thresholds when near "P <" or "P >"
_PVALUE_CONTEXT_RE = __import__('re').compile(
    r'P\s*[<>≤≥]\s*\d+\.\d+', __import__('re').IGNORECASE
)

# DOI prefix pattern: 10.XXXX... (4+ digits after the dot)
_DOI_PATTERN = __import__('re').compile(r'\b10\.\d{4,}')

# Virus variant/lineage pattern: letter.digits (e.g., B.1.351, BA.1.1, P.1)
_VARIANT_PATTERN = __import__('re').compile(
    r'\b[A-Z]{1,4}\.\d+(?:\.\d+)*\b'
)


def _is_pvalue_threshold(v: float, ctx: str) -> bool:
    """Check if this value is a P-value threshold rather than experimental data."""
    for t in _PVALUE_THRESHOLDS:
        if abs(v - t) < 1e-9:
            return True
    if _PVALUE_CONTEXT_RE.search(ctx):
        return True
    return False


def _is_metadata_number(v_str: str, ctx: str) -> bool:
    """Check if a number is metadata (DOI, variant name) rather than data."""
    if _DOI_PATTERN.search(ctx):
        return True
    if _VARIANT_PATTERN.search(ctx):
        return True
    return False


def _float_to_key(v: float, precision: int) -> str:
    """Convert float to string key at given precision for dedup."""
    return f"{v:.{precision}f}"


def detect_identical_values(values_with_ctx: list[dict]) -> dict:
    """Find identical values appearing in different contextual windows.

    Filters out:
      1. P-value thresholds (0.05, 0.01, etc.)
      2. Values clustered in the same table (all occurrences within 4000 chars
         or context windows sharing >50% word overlap — typical of large
         comparison matrices like genome identity tables)

    Returns dict with:
      - duplicates: list of {value, precision, occurrences, is_table}
      - suspicious_count: number of values repeated >= 3 times (excluding table clusters)
      - table_clustered: number of duplicate groups that are same-table artifacts
    """
    # Filter out P-value thresholds and metadata numbers (DOIs, variant names)
    filtered = [
        e for e in values_with_ctx
        if not _is_pvalue_threshold(e["value"], e["context"])
        and not _is_metadata_number(e["value_str"], e["context"])
    ]

    by_precision: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for entry in filtered:
        v = entry["value"]
        for prec in [2, 3, 4]:
            key = _float_to_key(v, prec)
            by_precision[prec][key].append(entry)

    def _is_table_cluster(entries: list[dict]) -> bool:
        """Check if all duplicates come from the same table/block."""
        if len(entries) < 2:
            return False
        positions = [e["start_pos"] for e in entries]
        char_span = max(positions) - min(positions)
        # All within 4000 chars → likely same table
        if char_span < 4000:
            return True
        # Check word overlap between context windows
        if len(entries) >= 3:
            word_sets = []
            for e in entries:
                words = set(re.sub(r'[^a-zA-Z]', ' ', e["context"].lower()).split())
                word_sets.append(words)
            # If all pairs share >50% words, it's the same table
            overlaps = 0
            total_pairs = 0
            for i in range(len(word_sets)):
                for j in range(i + 1, len(word_sets)):
                    if not word_sets[i] or not word_sets[j]:
                        continue
                    total_pairs += 1
                    intersection = word_sets[i] & word_sets[j]
                    union = word_sets[i] | word_sets[j]
                    if len(intersection) / max(len(union), 1) > 0.5:
                        overlaps += 1
            if total_pairs > 0 and overlaps / total_pairs > 0.6:
                return True
        return False

    # Find duplicates
    duplicates = []
    for prec in [2, 3, 4]:
        for key, entries in by_precision[prec].items():
            if len(entries) < 2:
                continue
            contexts = [e["context"] for e in entries]
            unique_contexts = set()
            for ctx in contexts:
                norm_ctx = re.sub(r'[^a-zA-Z]', ' ', ctx.lower())
                norm_ctx = ' '.join(norm_ctx.split())
                unique_contexts.add(norm_ctx[:60])
            if len(unique_contexts) >= 2:
                is_table = _is_table_cluster(entries)
                duplicates.append({
                    "value": float(key),
                    "precision": prec,
                    "count": len(entries),
                    "contexts": contexts[:5],
                    "is_table": is_table,
                })

    # Deduplicate: if same value flagged at multiple precisions, keep highest
    seen_vals: set[float] = set()
    unique_dups = []
    for d in sorted(duplicates, key=lambda x: x["precision"], reverse=True):
        if d["value"] not in seen_vals:
            unique_dups.append(d)
            seen_vals.add(d["value"])

    # Separate table artifacts from real signals
    table_dups = [d for d in unique_dups if d["is_table"]]
    real_dups = [d for d in unique_dups if not d["is_table"]]

    pvalue_hits = sum(1 for e in values_with_ctx if _is_pvalue_threshold(e["value"], e["context"]))
    metadata_hits = sum(1 for e in values_with_ctx if _is_metadata_number(e["value_str"], e["context"]))

    return {
        "duplicates": real_dups,  # Only return non-table duplicates as main result
        "table_duplicates": table_dups,
        "suspicious_count": sum(1 for d in real_dups if d["count"] >= 3),
        "total_duplicate_values": len(real_dups),
        "total_values_checked": len(filtered),
        "pvalue_filtered": pvalue_hits,
        "metadata_filtered": metadata_hits,
        "table_filtered": len(table_dups),
    }


def run_identical_values(paper) -> list[CheckResult]:
    """Detect identical numeric values across different experimental contexts."""
    MAX_TEXT = 500_000
    text = paper.results or paper.full_text
    text_truncated = len(text) > MAX_TEXT
    if text_truncated:
        text = text[:MAX_TEXT]

    if not text:
        return [
            CheckResult(
                check_id="numbers.identical_values",
                check_name="跨组相同数值检测",
                level="error",
                verdict="无法执行：未提取到文本",
                needs_human=False,
            )
        ]

    values_with_ctx = extract_values_with_context(text)
    if len(values_with_ctx) < 10:
        result = CheckResult(
            check_id="numbers.identical_values",
            check_name="跨组相同数值检测",
            level="error",
            verdict=f"数值样本不足（仅提取到 {len(values_with_ctx)} 个小数，需 ≥ 10）",
            needs_human=False,
        )
        return [result]

    detection = detect_identical_values(values_with_ctx)

    evidence = []
    for d in detection["duplicates"][:10]:
        evidence.append(
            f"数值 {d['value']} 在 {d['count']} 处不同上下文中出现（精度: {d['precision']} 位小数）"
        )
        for ctx in d["contexts"][:3]:
            evidence.append(f"  上下文: ...{ctx[:120]}...")

    trunc_note = "（文本已截断）" if text_truncated else ""
    total_dup = detection["total_duplicate_values"]
    suspicious = detection["suspicious_count"]

    pval_info = f"（已过滤 {detection['pvalue_filtered']} 个 P 值/统计阈值"
    if detection.get("metadata_filtered", 0) > 0:
        pval_info += f"，{detection['metadata_filtered']} 个 DOI/编号"
    if detection.get("table_filtered", 0) > 0:
        pval_info += f"，{detection['table_filtered']} 组表格重复"
    pval_info += "）"

    if suspicious >= 3:
        level = "red"
        verdict = f"发现 {suspicious} 组数值在 ≥3 处不同上下文中完全重复 {pval_info}{trunc_note}"
    elif total_dup >= 2:
        level = "orange"
        verdict = f"发现 {total_dup} 组数值在不同上下文中重复出现 {pval_info}{trunc_note}"
    elif total_dup == 1:
        level = "yellow"
        verdict = f"发现 1 组数值在不同上下文中重复 {pval_info}{trunc_note}"
    else:
        level = "green"
        verdict = (
            f"未发现跨组重复数值（共检测 {detection['total_values_checked']} 个非阈值数值"
            f"，过滤 {detection['pvalue_filtered']} 个 P 值/统计阈值）{trunc_note}"
        )

    return [
        CheckResult(
            check_id="numbers.identical_values",
            check_name="跨组相同数值检测",
            level=level,
            verdict=verdict,
            evidence=evidence[:15],
            confidence=0.8,
            needs_human=total_dup > 0,
            human_instruction="核对重复数值的原始上下文。若不同分析物/实验组出现完全相同数值（精度 ≥ 2 位小数），"
            "需向作者索取原始数据确认。注意区分合理重复（如相同浓度、时间点）。",
        )
    ]
