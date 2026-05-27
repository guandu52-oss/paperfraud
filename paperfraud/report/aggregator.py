"""Signal aggregation with weighted risk scoring.

Pipeline:
  1. Veto check: mathematical proof (GRIM/arithmetic red + high confidence) -> direct red
  2. Signal clustering: Jaccard similarity + page proximity -> correlation coefficients
  3. Weighted sum: Sum(signal_score * weight * correlation) + cluster_bonus
  4. Cap: min(score, 100)
  5. Floor: has_red -> max(score, 51), has_orange_no_red -> max(score, 31)
  6. Map to level and verdict
"""
from __future__ import annotations

import re

from paperfraud.base import CheckResult

# ── Stopwords for tokenization ──────────────────────────────────────────────
STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "to", "and", "or", "for", "with",
    "was", "were", "is", "are", "be", "by", "as", "at", "on", "from",
    "that", "this", "it", "not", "but", "we", "our", "their", "its",
    "has", "have", "had", "can", "may", "will", "would", "could",
    "also", "used", "using", "each", "all", "between",
})

# ── Detection weights ───────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "numbers.grim": 1.0,
    "numbers.statcheck": 0.9,
    "stats.sample_size": 0.85,
    "numbers.arithmetic": 0.8,
    "numbers.identical_values": 0.7,
    "bioinfo.western_blot": 0.6,
    "stats.method_misuse": 0.75,
    "stats.p_hacking": 0.5,
    "stats.normality_claim": 0.4,
    "numbers.benford": 0.4,
    "stats.fallacies": 0.4,
    "text.pvalue_camouflage": 0.3,
    "text.blacklist": 0.3,
    "numbers.digit": 0.3,
    "text.title_conclusion_gap": 0.3,
}

VETO_CHECKS = frozenset({"numbers.grim", "numbers.arithmetic"})

LEVEL_ORDER = {"red": 4, "orange": 3, "yellow": 2, "green": 1, "error": 0}

SIGNAL_SCORES = {"red": 25, "orange": 12, "yellow": 5}


# ── Tokenization & similarity ───────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"\b[\w.]+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── Signal clustering ───────────────────────────────────────────────────────

def _compute_correlations(results: list[CheckResult]) -> dict[str, float]:
    """For each non-green result, compute correlation coefficient vs peers.

    Returns: {check_id: correlation_coefficient}
      1.5 = same-cluster (same/adjacent page + Jaccard >= 0.3)
      1.2 = weak-cluster (same page + Jaccard >= 0.15)
      1.0 = isolated
    """
    non_green = [r for r in results if r.level in ("red", "orange", "yellow")]
    if len(non_green) < 2:
        return {r.check_id: 1.0 for r in non_green}

    # Pre-tokenize excerpts
    tokens: dict[str, set[str]] = {}
    pages: dict[str, int] = {}
    for r in non_green:
        excerpts = []
        for sl in r.source_locations:
            if sl.excerpt:
                excerpts.append(sl.excerpt)
        combined = " ".join(excerpts)
        tokens[r.check_id] = _tokenize(combined)
        pages[r.check_id] = r.source_locations[0].page if r.source_locations else -1

    # Initialize all as isolated
    correlations = {r.check_id: 1.0 for r in non_green}

    # Pairwise comparison
    for i in range(len(non_green)):
        for j in range(i + 1, len(non_green)):
            ri, rj = non_green[i], non_green[j]
            ji = _jaccard(tokens[ri.check_id], tokens[rj.check_id])
            pi, pj = pages[ri.check_id], pages[rj.check_id]

            same_or_adjacent = (pi == pj) or (pi >= 0 and pj >= 0 and abs(pi - pj) == 1)

            if same_or_adjacent and ji >= 0.3:
                correlations[ri.check_id] = max(correlations[ri.check_id], 1.5)
                correlations[rj.check_id] = max(correlations[rj.check_id], 1.5)
            elif pi == pj and ji >= 0.15:
                correlations[ri.check_id] = max(correlations[ri.check_id], 1.2)
                correlations[rj.check_id] = max(correlations[rj.check_id], 1.2)

    return correlations


# ── Veto ─────────────────────────────────────────────────────────────────────

def _veto_override(results: list[CheckResult]) -> dict | None:
    for r in results:
        if r.check_id in VETO_CHECKS and r.level == "red" and r.confidence >= 0.9:
            return {
                "overall_level": "red",
                "overall_verdict": (
                    f"检测到数学铁证（{r.check_name}）："
                    f"基础数据在数学上不可能。建议立即审查原始数据。"
                ),
                "veto_trigger": r.check_id,
            }
    return None


# ── Risk score computation ──────────────────────────────────────────────────

def _compute_risk_score(results: list[CheckResult]) -> tuple[float, dict]:
    """Compute the 0-100 Fraud Risk Score.

    Returns: (score, breakdown_dict)
    """
    non_green = [r for r in results if r.level in ("red", "orange", "yellow")]
    if not non_green:
        return 0.0, {
            "total": 0,
            "contributions": [],
            "cluster_bonus": 0,
            "veto_triggered": False,
            "floor_applied": False,
        }

    correlations = _compute_correlations(results)
    contributions = []

    for r in non_green:
        signal_score = SIGNAL_SCORES.get(r.level, 0)
        weight = WEIGHTS.get(r.check_id, 0.5)
        corr = correlations.get(r.check_id, 1.0)
        contrib = signal_score * weight * corr
        contributions.append({
            "check_id": r.check_id,
            "check_name": r.check_name,
            "level": r.level,
            "signal_score": signal_score,
            "weight": weight,
            "correlation": corr,
            "contribution": round(contrib, 2),
        })

    base_score = sum(c["contribution"] for c in contributions)

    # Cluster bonus
    clustered_ids = [cid for cid, corr in correlations.items() if corr > 1.0]
    n_clustered = len(clustered_ids)
    if n_clustered >= 5:
        cluster_bonus = 20
    elif n_clustered >= 3:
        cluster_bonus = 10
    else:
        cluster_bonus = 0

    total = base_score + cluster_bonus

    return total, {
        "total": round(total, 1),
        "contributions": contributions,
        "cluster_bonus": cluster_bonus,
        "veto_triggered": False,
        "floor_applied": False,
    }


# ── Human review ranking ────────────────────────────────────────────────────

def _rank_human_review(
    results: list[CheckResult],
    weights: dict[str, float],
    correlations: dict[str, float],
) -> list[dict]:
    scored = []
    for r in results:
        if not r.needs_human:
            continue
        signal_score = SIGNAL_SCORES.get(r.level, 5)
        contribution = (
            signal_score
            * weights.get(r.check_id, 0.5)
            * correlations.get(r.check_id, 1.0)
        )
        scored.append((contribution, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r.to_dict() for _, r in scored]


# ── Main entry point ────────────────────────────────────────────────────────

def aggregate_results(results: list[CheckResult]) -> dict:
    # Counts
    levels = {"red": 0, "orange": 0, "yellow": 0, "green": 0, "error": 0}
    for r in results:
        levels[r.level] = levels.get(r.level, 0) + 1

    # Step 1: Veto
    veto = _veto_override(results)
    if veto:
        return {
            "red_count": levels["red"],
            "orange_count": levels["orange"],
            "yellow_count": levels["yellow"],
            "green_count": levels["green"],
            "error_count": levels["error"],
            "total_checks": len(results),
            "overall_level": veto["overall_level"],
            "overall_verdict": veto["overall_verdict"],
            "risk_score": 100,
            "risk_breakdown": {
                "total": 100,
                "contributions": [],
                "cluster_bonus": 0,
                "veto_triggered": True,
                "floor_applied": False,
            },
            "needs_human": [r.to_dict() for r in results if r.needs_human],
            "needs_human_count": sum(1 for r in results if r.needs_human),
        }

    # Step 2-4: Compute risk score
    raw_score, breakdown = _compute_risk_score(results)

    # Step 4: Cap
    risk_score = min(raw_score, 100.0)

    # Step 5: Floor
    max_level = max(results, key=lambda r: LEVEL_ORDER.get(r.level, 0)).level if results else "green"
    floor_applied = False
    if max_level == "red" and risk_score < 51:
        risk_score = 51.0
        floor_applied = True
    elif max_level == "orange" and risk_score < 31:
        risk_score = 31.0
        floor_applied = True

    breakdown["total"] = round(risk_score, 1)
    breakdown["floor_applied"] = floor_applied

    # Step 6: Map to level and verdict
    if risk_score >= 51:
        overall = "red"
    elif risk_score >= 31:
        overall = "orange"
    elif risk_score >= 16:
        overall = "yellow"
    else:
        overall = "green"

    if overall == "red":
        overall_verdict = "高度可疑——多维度信号交叉印证，建议深入调查"
    elif overall == "orange":
        overall_verdict = "多处可疑信号，建议人工逐条复核"
    elif overall == "yellow":
        overall_verdict = "存在孤立疑点，可能为检测噪声或文本提取误差"
    elif levels["error"] == len(results):
        overall_verdict = "无法判定——所有检查因数据不足无法执行"
    else:
        overall_verdict = "未发现系统性造假信号"

    # Rank human review by contribution
    correlations = _compute_correlations(results)
    needs_human = _rank_human_review(results, WEIGHTS, correlations)

    return {
        "red_count": levels["red"],
        "orange_count": levels["orange"],
        "yellow_count": levels["yellow"],
        "green_count": levels["green"],
        "error_count": levels["error"],
        "total_checks": len(results),
        "overall_level": overall,
        "overall_verdict": overall_verdict,
        "risk_score": risk_score,
        "risk_breakdown": breakdown,
        "needs_human": needs_human,
        "needs_human_count": len(needs_human),
    }
