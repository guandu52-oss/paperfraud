# LLM 审查层 + 综合判定逻辑 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the naive "one red = overall red" aggregator with a 0-100 Weighted Risk Scoring engine with Veto/Floor/Cap, and overhaul the LLM review prompt with Methods/Results context injection, few-shot calibration, and increased token budget.

**Architecture:** `aggregator.py` becomes a multi-stage scoring pipeline (Veto → Clustering → Weighted Sum → Cap → Floor → Map). `prompts.py` gains keyword-targeted evidence extraction from Methods/Results and two few-shot calibration examples (one positive, one false-positive). `llm_review.py` gets parameter bumps. `cli.py` and `formatter.py` adapt to new `aggregated` fields (`risk_score`, `risk_breakdown`).

**Tech Stack:** Pure Python 3.9+ (stdlib only for aggregator — no new dependencies). scipy already in deps.

---

## File Map

| File | Responsibility | Change |
|------|---------------|--------|
| `paperfraud/report/aggregator.py` | Risk scoring engine + signal clustering + veto/floor/cap pipeline | Full rewrite (~200 lines) |
| `tests/test_aggregator.py` | Unit tests for scoring, veto, floor, clustering | New file (~120 lines) |
| `paperfraud/review/prompts.py` | System prompt, evidence extraction, few-shot examples | Major rewrite (~120 lines total) |
| `paperfraud/review/llm_review.py` | max_tokens + temperature bumps | 6 lines changed |
| `paperfraud/cli.py` | Terminal report: show risk_score + breakdown | ~20 lines added |
| `paperfraud/report/formatter.py` | JSON/Markdown: include risk_score fields | ~15 lines added |

---

### Task 1: Rewrite aggregator.py — Core Scoring Engine

**Files:**
- Create: `tests/test_aggregator.py`
- Modify: `paperfraud/report/aggregator.py` (full rewrite)

- [ ] **Step 1: Write failing tests for aggregator**

```python
# tests/test_aggregator.py
"""Unit tests for the weighted risk scoring engine."""
from __future__ import annotations

import pytest
from paperfraud.base import CheckResult, SourceLocation
from paperfraud.report.aggregator import (
    _tokenize,
    _jaccard,
    _compute_correlations,
    _veto_override,
    _compute_risk_score,
    _rank_human_review,
    WEIGHTS,
    LEVEL_ORDER,
    aggregate_results,
)


class TestTokenize:
    def test_extracts_numbers_and_terms(self):
        tokens = _tokenize("n=3 mice per group, p<0.001 with SD=0.42")
        assert "0.001" in tokens or "0" in tokens
        assert "mice" in tokens
        assert "group" in tokens
        assert "0.42" in tokens or "42" in tokens

    def test_filters_stopwords(self):
        tokens = _tokenize("the mice were in the cage with a treatment")
        assert "the" not in tokens
        assert "were" not in tokens
        assert "in" not in tokens
        assert "a" not in tokens
        assert "with" not in tokens
        assert "mice" in tokens

    def test_handles_empty_text(self):
        assert _tokenize("") == set()
        assert _tokenize("the a an of in to") == set()


class TestJaccard:
    def test_identical_sets(self):
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert result == 2 / 4  # intersection {b,c}=2, union {a,b,c,d}=4

    def test_empty_input(self):
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0


class TestVetoOverride:
    def test_grim_red_high_confidence_triggers_veto(self):
        results = [
            CheckResult(
                check_id="numbers.grim", check_name="GRIM",
                level="red", verdict="GRIM inconsistent",
                confidence=0.95, source_locations=[
                    SourceLocation(page=3, excerpt="M=4.37, N=12")
                ],
            ),
            CheckResult(
                check_id="numbers.statcheck", check_name="Statcheck",
                level="green", verdict="all good", confidence=0.85,
            ),
        ]
        veto = _veto_override(results)
        assert veto is not None
        assert veto["overall_level"] == "red"
        assert veto["veto_trigger"] == "numbers.grim"
        assert "数学" in veto["overall_verdict"]

    def test_grim_red_low_confidence_no_veto(self):
        results = [
            CheckResult(
                check_id="numbers.grim", check_name="GRIM",
                level="red", verdict="GRIM inconsistent",
                confidence=0.8,  # below 0.9 threshold
            ),
        ]
        assert _veto_override(results) is None

    def test_arithmetic_red_high_confidence_triggers_veto(self):
        results = [
            CheckResult(
                check_id="numbers.arithmetic", check_name="Arithmetic",
                level="red", verdict="sum mismatch",
                confidence=0.95,
            ),
        ]
        veto = _veto_override(results)
        assert veto is not None
        assert veto["veto_trigger"] == "numbers.arithmetic"

    def test_non_veto_check_no_veto(self):
        results = [
            CheckResult(
                check_id="stats.sample_size", check_name="Sample Size",
                level="red", verdict="n<3", confidence=0.95,
            ),
        ]
        assert _veto_override(results) is None


class TestCorrelations:
    def test_same_page_similar_excerpt_correlated(self):
        results = [
            CheckResult(
                check_id="numbers.grim", check_name="GRIM",
                level="red", verdict="M=4.37,N=12 inconsistent",
                source_locations=[
                    SourceLocation(page=3, excerpt="M=4.37 SD=0.82 N=12 p<0.01")
                ],
            ),
            CheckResult(
                check_id="numbers.statcheck", check_name="Statcheck",
                level="red", verdict="p=0.01 inconsistent",
                source_locations=[
                    SourceLocation(page=3, excerpt="t(22)=3.1 p=0.01 N=12 M=4.37")
                ],
            ),
        ]
        corr = _compute_correlations(results)
        assert corr["numbers.grim"] == 1.5
        assert corr["numbers.statcheck"] == 1.5

    def test_different_pages_not_correlated(self):
        results = [
            CheckResult(
                check_id="numbers.grim", check_name="GRIM",
                level="red", verdict="inconsistent",
                source_locations=[
                    SourceLocation(page=3, excerpt="M=4.37 N=12")
                ],
            ),
            CheckResult(
                check_id="stats.sample_size", check_name="Sample Size",
                level="red", verdict="n<3",
                source_locations=[
                    SourceLocation(page=8, excerpt="n=2 per group")
                ],
            ),
        ]
        corr = _compute_correlations(results)
        assert corr["numbers.grim"] == 1.0
        assert corr["stats.sample_size"] == 1.0

    def test_adjacent_pages_correlated(self):
        results = [
            CheckResult(
                check_id="numbers.grim", check_name="GRIM",
                level="red", verdict="inconsistent",
                source_locations=[
                    SourceLocation(page=3, excerpt="M=4.37 SD=0.82 N=12 p<0.01 t(22)=3.1")
                ],
            ),
            CheckResult(
                check_id="numbers.statcheck", check_name="Statcheck",
                level="red", verdict="inconsistent",
                source_locations=[
                    SourceLocation(page=4, excerpt="t(22)=3.1 p=0.01 N=12 M=4.37 SD=0.82")
                ],
            ),
        ]
        corr = _compute_correlations(results)
        assert corr["numbers.grim"] == 1.5
        assert corr["numbers.statcheck"] == 1.5

    def test_no_source_locations_default_isolated(self):
        results = [
            CheckResult(
                check_id="text.blacklist", check_name="Blacklist",
                level="yellow", verdict="flagged phrase",
                source_locations=[],
            ),
            CheckResult(
                check_id="text.pvalue_camouflage", check_name="Camouflage",
                level="yellow", verdict="p-value wording",
                source_locations=[],
            ),
        ]
        corr = _compute_correlations(results)
        assert corr["text.blacklist"] == 1.0
        assert corr["text.pvalue_camouflage"] == 1.0


class TestRiskScore:
    def test_all_green_zero_score(self):
        results = [
            CheckResult(check_id="numbers.grim", check_name="GRIM",
                        level="green", verdict="all good"),
            CheckResult(check_id="numbers.statcheck", check_name="Statcheck",
                        level="green", verdict="all good"),
        ]
        score, breakdown = _compute_risk_score(results)
        assert score == 0

    def test_single_red_with_weight(self):
        results = [
            CheckResult(check_id="numbers.grim", check_name="GRIM",
                        level="red", verdict="inconsistent", confidence=0.95,
                        source_locations=[SourceLocation(page=1, excerpt="M=4.37,N=12")]),
            CheckResult(check_id="numbers.statcheck", check_name="Statcheck",
                        level="green", verdict="all good"),
        ]
        score, breakdown = _compute_risk_score(results)
        expected = 25 * 1.0 * 1.0  # red=25, grim_weight=1.0, isolated=1.0
        assert score == expected

    def test_cluster_bonus_three_signals(self):
        results = [
            CheckResult(check_id="numbers.grim", check_name="GRIM",
                        level="red", verdict="inconsistent",
                        source_locations=[SourceLocation(page=3, excerpt="M=4.37 N=12 p<0.01")]),
            CheckResult(check_id="numbers.statcheck", check_name="Statcheck",
                        level="red", verdict="inconsistent",
                        source_locations=[SourceLocation(page=3, excerpt="t(22)=3.1 p=0.01 N=12")]),
            CheckResult(check_id="stats.sample_size", check_name="Sample Size",
                        level="red", verdict="n=12 small",
                        source_locations=[SourceLocation(page=3, excerpt="N=12 p<0.01 M=4.37")]),
        ]
        score, breakdown = _compute_risk_score(results)
        # All correlated at 1.5 (same page, similar excerpt), cluster_bonus = +10
        assert breakdown["cluster_bonus"] == 10
        assert score > 25 * 3  # cluster bonus added

    def test_orange_and_yellow_mix(self):
        results = [
            CheckResult(check_id="stats.p_hacking", check_name="P-hacking",
                        level="orange", verdict="p-curve anomaly",
                        source_locations=[SourceLocation(page=5, excerpt="p=0.04")]),
            CheckResult(check_id="text.blacklist", check_name="Blacklist",
                        level="yellow", verdict="flagged terms",
                        source_locations=[]),
        ]
        score, breakdown = _compute_risk_score(results)
        expected = 12 * 0.5 * 1.0 + 5 * 0.3 * 1.0  # orange p_hacking + yellow blacklist
        assert score == expected


class TestFloor:
    def test_red_floor_minimum_51(self):
        """One isolated red with low weight should still get floor=51."""
        results = [
            CheckResult(check_id="text.blacklist", check_name="Blacklist",
                        level="red", verdict="critical phrase",
                        source_locations=[]),
        ] + [CheckResult(check_id=f"check.{i}", check_name=f"Check {i}",
                          level="green", verdict="ok") for i in range(10)]
        aggregated = aggregate_results(results)
        assert aggregated["risk_score"] >= 51
        assert aggregated["overall_level"] == "red"

    def test_orange_floor_minimum_31(self):
        results = [
            CheckResult(check_id="text.blacklist", check_name="Blacklist",
                        level="orange", verdict="suspicious",
                        source_locations=[]),
        ] + [CheckResult(check_id=f"check.{i}", check_name=f"Check {i}",
                          level="green", verdict="ok") for i in range(10)]
        aggregated = aggregate_results(results)
        assert aggregated["risk_score"] >= 31
        assert aggregated["overall_level"] == "orange"


class TestHumanReviewRanking:
    def test_ranked_by_contribution(self):
        results = [
            CheckResult(check_id="numbers.grim", check_name="GRIM",
                        level="red", verdict="inconsistent",
                        needs_human=True, confidence=0.95,
                        source_locations=[SourceLocation(page=1, excerpt="M=4.37,N=12")]),
            CheckResult(check_id="text.blacklist", check_name="Blacklist",
                        level="yellow", verdict="phrase",
                        needs_human=True,
                        source_locations=[]),
        ]
        ranked = _rank_human_review(results, WEIGHTS, {"numbers.grim": 1.0, "text.blacklist": 1.0})
        assert len(ranked) == 2
        assert ranked[0]["check_id"] == "numbers.grim"  # higher contribution first
        assert ranked[1]["check_id"] == "text.blacklist"


class TestAggregateResults:
    def test_returns_all_required_fields(self):
        results = [
            CheckResult(check_id="numbers.grim", check_name="GRIM",
                        level="green", verdict="ok"),
        ]
        agg = aggregate_results(results)
        assert "risk_score" in agg
        assert "risk_breakdown" in agg
        assert "overall_level" in agg
        assert "overall_verdict" in agg
        assert "needs_human" in agg
        assert isinstance(agg["needs_human"], list)

    def test_veto_short_circuits_scoring(self):
        results = [
            CheckResult(check_id="numbers.grim", check_name="GRIM",
                        level="red", verdict="GRIM inconsistent",
                        confidence=0.95,
                        source_locations=[SourceLocation(page=1, excerpt="M=4.37,N=12")]),
        ]
        agg = aggregate_results(results)
        assert agg["overall_level"] == "red"
        assert agg["risk_breakdown"]["veto_triggered"] is True
        assert "数学铁证" in agg["overall_verdict"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m pytest tests/test_aggregator.py -v
```

Expected: All tests FAIL — `ModuleNotFoundError` or `ImportError` for new functions.

- [ ] **Step 3: Write aggregator.py**

```python
"""Signal aggregation with weighted risk scoring.

Pipeline:
  1. Veto check: mathematical proof (GRIM/arithmetic red + high confidence) → direct red
  2. Signal clustering: Jaccard similarity + page proximity → correlation coefficients
  3. Weighted sum: Σ(signal_score × weight × correlation) + cluster_bonus
  4. Cap: min(score, 100)
  5. Floor: has_red → max(score, 51), has_orange_no_red → max(score, 31)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m pytest tests/test_aggregator.py -v
```

Expected: All 23 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && git add tests/test_aggregator.py paperfraud/report/aggregator.py && git commit -m "feat: rewrite aggregator with weighted risk scoring engine

- Replace simple red-count logic with 0-100 Fraud Risk Score
- Add detection weights (GRIM=1.0 ... blacklist=0.3) quantifying domain knowledge
- Add Jaccard-based signal clustering for cross-signal correlation
- Add Veto override for mathematical proof (GRIM/arithmetic red + confidence>=0.9)
- Add Floor mechanism to prevent single-red dilution
- Add Cap at 100 for UI consistency
- Rank human review items by risk contribution"
```

---

### Task 2: Rewrite prompts.py — System Prompt + Evidence Extraction + Few-shot

**Files:**
- Modify: `paperfraud/review/prompts.py`

- [ ] **Step 1: Write the new prompts.py**

```python
"""Prompt templates for LLM qualitative review.

Key improvements over v1:
  - Methods/Results keyword-targeted evidence injection (not blind abstract truncation)
  - Experiment-type-aware judgment guides (cell vs animal vs clinical)
  - Two few-shot calibration examples (one positive, one false-positive)
  - Fixed contradiction: image signals are NOT auto-determinative
"""
from __future__ import annotations

from paperfraud.base import CheckResult, ParsedPaper

# ── Keywords for evidence paragraph extraction ──────────────────────────────
EVIDENCE_KEYWORDS = [
    "n=", "n =", "p <", "p =", "p>",
    "mice", "cells", "patients",
    "triplicate", "SD", "SEM", "±",  # ±
    "western", "loading", "biological",
    "independent", "experiment", "replicate",
]

SYSTEM_PROMPT = """你是一位学术论文造假审查专家。你会收到一份自动化检测报告，包含：
1. 论文元数据（标题、期刊、Methods/Results 关键段落）
2. 每个 Red/Orange/Yellow 信号的 evidence 原文上下文（"案发现场"）
3. 所有自动化检测的完整结果

你的任务：
- 逐条审查每个 Red/Orange/Yellow 信号，判断是"真锤"（确实可疑）还是"假阳性"（检测噪声/正常模式）
- 跨信号关联分析：如果多项信号指向同一底层问题（如"统计素养系统性缺陷"），请明确指出
- 撰写一段可直接发表在 PubPeer 上的中文审稿意见（必须包含具体证据引用，不能泛泛而谈）

### 实验类型判断指南
- 细胞/分子实验 (in vitro)：n=3 是常见设计，但需结合 loading control 和重复次数判断
- 动物实验 (in vivo)：n=3-5 功效极低，p<0.01 需审查原始数据
- 临床/人体研究：小样本不具有统计意义，需区分探索性 vs 验证性研究

### 技术重复 vs 生物学重复
- "in triplicate"、"3 wells per sample"、"technical replicates" → 技术重复，不是生物学 n
- 若论文将技术重复当作生物学重复来跑统计检验（伪重复/pseudoreplication），标记为 true_positive
- 若原文声明了技术重复但在其他段落使用了足够的生物学重复，标记为 false_alarm

### 跨信号关联
- ≥2 个信号指向同一段落同一组数据 → 互相印证，可信度显著提升
- 孤立信号来自 PDF 文本提取噪声（行内换行导致 p 值被分割、连字符截断等）→ 倾向于假阳性
- 多个信号分布在 Methods/Results/图注的不同位置但指向同一统计问题 → 系统性缺陷，severity 升级

### 判断标准
- confidence < 0.5 的信号：倾向于假阳性，除非 evidence 原文非常明确
- PDF 文本提取来源的信号：天然噪声大，需谨慎判定
- 统计/文本信号若指向同一问题 → 互相印证，可信度提升
- 论文 Methods 中声明的实验设计信息应作为主要判断依据

输出要求：严格 JSON 格式，无额外文字、无 markdown 标记。

---

## 审查案例 1（造假论文 — 正样本校准）

论文：动物实验，n=3/group，声称 p<0.001
Methods 关键信息：C57BL/6 mice, n=3 per group, Student's t-test, p<0.05 considered significant

检测信号：
  - stats.sample_size: RED — n=3 声称 p<0.001，需 d≥3.5，动物实验中几乎不可能
  - numbers.grim: RED — M=4.37, N=12 → N×M=52.44，非整数，GRIM 不一致
  - stats.p_hacking: RED — 8/11 个精确 p 值在 0.04-0.05 区间
  - text.blacklist: YELLOW — "significantly increased" 出现 8 次

正确审查输出：
```json
{
  "overall_assessment": "多维度信号交叉印证：小样本+极端声称+GRIM数学不一致+p-curve异常。信号分布在Methods和Results不同段落，非孤立噪声。动物实验n=3声称p<0.001在生物学上极不合理。",
  "severity_score": 9,
  "signal_reviews": [
    {
      "check_id": "stats.sample_size",
      "is_true_positive": true,
      "reasoning": "动物实验n=3声称p<0.001需效应量d≥3.5，远超动物实验典型范围(d=0.8-2.0)。Methods确认n=3，非文本提取误差。",
      "severity": "high"
    },
    {
      "check_id": "numbers.grim",
      "is_true_positive": true,
      "reasoning": "M=4.37 × N=12 = 52.44，非整数，数学上不可能来自整数测量值。GRIM为数学确定性检验，假阳性率极低。",
      "severity": "high"
    },
    {
      "check_id": "stats.p_hacking",
      "is_true_positive": true,
      "reasoning": "8/11个p值集中在0.04-0.05，为经典p-hacking模式。与样本量不足形成逻辑闭环：小样本无法达到显著→反复调整分析→p值聚集在阈值附近。",
      "severity": "high"
    },
    {
      "check_id": "text.blacklist",
      "is_true_positive": false,
      "reasoning": "significantly increased是生命科学标准统计用语，非造假话术。8次出现在合理范围内。",
      "severity": "false_alarm"
    }
  ],
  "pubpeer_draft": "该文报告n=3/group C57BL/6小鼠实验并声称p<0.001。达到此显著性需Cohen's d≥3.5，远超动物实验合理效应量范围(d=0.8-2.0)。同时GRIM检验显示报告均值M=4.37与样本量N=12数学上不兼容(N×M=52.44非整数)。p-curve分析显示8/11个精确p值集中于0.04-0.05区间，呈经典p-hacking模式。三项独立信号从样本量合理性、基础算术一致性、p值分布三个维度交叉印证，强烈提示数据不可靠。建议作者提供原始数据。"
}
```

---

## 审查案例 2（假阳性 — 负样本校准）

论文：细胞实验，Western blot + qPCR
Methods 关键信息：HEK293T cells, Western blot was performed in triplicate, qPCR with three biological replicates

检测信号：
  - stats.sample_size: YELLOW — 全文检测到 n=3
  - text.blacklist: YELLOW — "dramatically increased" 出现 2 次

正确审查输出：
```json
{
  "overall_assessment": "仅有的黄色信号均可解释为假阳性。'n=3'实为Methods中声明的技术重复(in triplicate)，且qPCR使用三个生物学重复。'dramatically increased'为偶发修辞，非系统性话术模式。论文整体方法学描述完整。",
  "severity_score": 1,
  "signal_reviews": [
    {
      "check_id": "stats.sample_size",
      "is_true_positive": false,
      "reasoning": "Methods明确声明Western blot was performed in triplicate——这是技术重复，不是生物学n=3。且qPCR部分使用了three biological replicates。自动化检测无法区分技术vs生物学重复，需人工判定为假阳性。",
      "severity": "false_alarm"
    },
    {
      "check_id": "text.blacklist",
      "is_true_positive": false,
      "reasoning": "dramatically increased出现仅2次，属正常科学写作中的偶发修辞，非黑名单话术的系统性使用。",
      "severity": "false_alarm"
    }
  ],
  "pubpeer_draft": ""
}
```"""


def _extract_evidence_paragraphs(
    text: str, keywords: list[str], max_chars: int = 3000
) -> str:
    """Extract paragraphs containing statistical/methodological keywords.

    Avoids blind text truncation — only feeds the LLM "meat" (paragraphs
    that actually contain n=, p<, SD, SEM, western, triplicate, etc.).
    """
    if not text:
        return ""

    paragraphs = text.split("\n\n")
    hits = []
    for para in paragraphs:
        para_lower = para.lower()
        if any(kw.lower() in para_lower for kw in keywords):
            hits.append(para.strip())

    if not hits:
        return ""

    result = ""
    for h in hits:
        if len(result) + len(h) + 2 > max_chars:
            remaining = max_chars - len(result)
            if remaining > 200:
                result += h[:remaining] + "..."
            break
        result += h + "\n\n"

    return result.strip()


def build_review_prompt(
    paper: ParsedPaper, aggregated: dict, results: list[CheckResult]
) -> str:
    """Build the user prompt for LLM review.

    Key changes from v1:
      - Methods/Results keyword-targeted paragraphs replace blind abstract truncation
      - Evidence injection carries section labels for context
    """
    sections = []

    # ── Paper metadata ──────────────────────────────────────────────────
    sections.append("# 论文信息")
    sections.append(f"标题：{paper.title or '未知'}")
    if paper.journal:
        sections.append(f"期刊：{paper.journal}")
    if paper.year:
        sections.append(f"年份：{paper.year}")
    if paper.authors:
        authors_str = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors_str += f" 等 ({len(paper.authors)} 人)"
        sections.append(f"作者：{authors_str}")
    sections.append("")

    # ── Methods/Results key paragraphs (the "meat") ─────────────────────
    if paper.methods:
        methods_evidence = _extract_evidence_paragraphs(
            paper.methods, EVIDENCE_KEYWORDS, max_chars=3000
        )
        if methods_evidence:
            sections.append("# Methods 关键段落（实验设计信息）")
            sections.append(methods_evidence)
            sections.append("")

    if paper.results:
        results_evidence = _extract_evidence_paragraphs(
            paper.results, EVIDENCE_KEYWORDS, max_chars=3000
        )
        if results_evidence:
            sections.append("# Results 关键段落（统计报告信息）")
            sections.append(results_evidence)
            sections.append("")

    # ── Abstract (kept for overview, lower priority) ────────────────────
    if paper.abstract:
        abstract = paper.abstract[:1500]
        sections.append("# 摘要（仅作概览参考）")
        sections.append(abstract)
        sections.append("")

    # ── Signal review ───────────────────────────────────────────────────
    level_labels = {
        "red": "红色信号（严重）",
        "orange": "橙色信号（可疑）",
        "yellow": "黄色信号（需关注）",
    }

    for level in ["red", "orange", "yellow"]:
        level_results = [r for r in results if r.level == level]
        if not level_results:
            continue

        sections.append(f"# {level_labels[level]}")
        sections.append("")

        for i, r in enumerate(level_results, 1):
            sections.append(f"## {i}. [{r.check_id}] {r.check_name}")
            sections.append(f"判定：{r.verdict}")
            sections.append(f"置信度：{r.confidence:.0%}")
            if r.evidence:
                sections.append("证据原文（案发现场）：")
                for e in r.evidence[:8]:
                    sections.append(f"  - {e}")
            if r.human_instruction:
                sections.append(f"人工复核指引：{r.human_instruction}")
            sections.append("")

    # ── Green / Error summary ───────────────────────────────────────────
    green_results = [r for r in results if r.level == "green"]
    if green_results:
        sections.append("# 绿色信号（已通过）")
        sections.append(f"以下 {len(green_results)} 项检测未发现异常：")
        for r in green_results:
            sections.append(f"  - {r.check_name}")
        sections.append("")

    error_results = [r for r in results if r.level == "error"]
    if error_results:
        sections.append("# 无法执行的检测")
        for r in error_results:
            sections.append(f"  - {r.check_name}: {r.verdict}")
        sections.append("")

    # ── JSON output instruction ─────────────────────────────────────────
    sections.append("---")
    sections.append(
        "请基于以上信息，输出以下 **严格的 JSON 格式**"
        "（字段名必须完全一致，不得修改）："
    )
    sections.append("")
    sections.append("```json")
    sections.append("{")
    sections.append(
        '  "overall_assessment": '
        '"综合判断中文文本，1-2段，总结关键发现和整体可信度评估",'
    )
    sections.append('  "severity_score": 7,')
    sections.append('  "signal_reviews": [')
    sections.append("    {")
    sections.append('      "check_id": "check.id",')
    sections.append('      "is_true_positive": true,')
    sections.append('      "reasoning": "判定理由",')
    sections.append('      "severity": "high"')
    sections.append("    }")
    sections.append("  ],")
    sections.append(
        '  "pubpeer_draft": '
        '"可直接发表在 PubPeer 上的中文审稿意见草稿，需引用具体证据"'
    )
    sections.append("}")
    sections.append("```")
    sections.append("")
    sections.append("字段说明：")
    sections.append("- overall_assessment: 综合中文判断（必填）")
    sections.append("- severity_score: 0-10 造假嫌疑评分（必填）")
    sections.append(
        "  0-2: 无造假信号  3-4: 有疑点  5-6: 值得关注  "
        "7-8: 高度怀疑  9-10: 铁证"
    )
    sections.append(
        "- signal_reviews: 每个 Red/Orange/Yellow 信号的审查"
        "（必填，至少包含所有非绿色信号）"
    )
    sections.append("  - check_id: 检测项 ID（必填）")
    sections.append("  - is_true_positive: true=真锤, false=假阳性（必填）")
    sections.append("  - reasoning: 判定理由（必填）")
    sections.append(
        "  - severity: 严重程度 high/medium/low/false_alarm（必填）"
    )
    sections.append(
        "- pubpeer_draft: 中文审稿草稿，可公开发表，需引用具体证据（必填）"
    )

    return "\n".join(sections)
```

- [ ] **Step 2: Verify the module imports correctly**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -c "from paperfraud.review.prompts import SYSTEM_PROMPT, build_review_prompt, _extract_evidence_paragraphs; print('SYSTEM_PROMPT length:', len(SYSTEM_PROMPT)); print('OK')"
```

Expected: `SYSTEM_PROMPT length: <number>` followed by `OK`, no errors.

- [ ] **Step 3: Test evidence extraction on sample text**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -c "
from paperfraud.review.prompts import _extract_evidence_paragraphs, EVIDENCE_KEYWORDS
sample = '''The mice were housed at 22C.

Western blot was performed in triplicate with n=3 per group. SD=0.42.

Results showed p<0.05 for all comparisons.

Cells were cultured in DMEM with 10% FBS at 37C.'''
result = _extract_evidence_paragraphs(sample, EVIDENCE_KEYWORDS, max_chars=3000)
print(result)
assert 'triplicate' in result
assert 'n=3' in result
assert 'p<0.05' in result
assert 'DMEM' not in result  # no keywords matched
print('Evidence extraction: OK')
"
```

Expected: Extracted paragraphs contain `triplicate`, `n=3`, `p<0.05`, does NOT contain `DMEM`. Output ends with `OK`.

- [ ] **Step 4: Commit**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && git add paperfraud/review/prompts.py && git commit -m "feat: rewrite LLM review prompt with Methods/Results injection and few-shot calibration

- Replace blind abstract truncation with keyword-targeted evidence extraction
  from Methods and Results sections (EVIDENCE_KEYWORDS)
- Add experiment-type-aware judgment guide (cell vs animal vs clinical)
- Add technical vs biological replicate distinction guide
- Add cross-signal correlation guidance
- Two few-shot calibration examples (positive + false-positive)
- Remove contradictory image signal weight claim
- Remove unused _smart_truncate function"
```

---

### Task 3: Update llm_review.py — Token Budget + Temperature

**Files:**
- Modify: `paperfraud/review/llm_review.py` (lines 160, 270, 231)

- [ ] **Step 1: Change max_tokens and temperature in all three providers**

Three edits in `llm_review.py`. Each uses surrounding context to guarantee uniqueness:

**Edit 1 — DeepSeek (`_call_deepseek`, ~line 160):**
```
old_string: "        temperature=0.1,\n        max_tokens=4096,\n    )\n\n    raw = response.choices[0].message.content or \"\"\n    tokens = response.usage.total_tokens if response.usage else 0\n    data = _parse_json_response(raw)\n    return _dict_to_result(data, \"deepseek\", model, tokens)"
new_string: "        temperature=0.05,\n        max_tokens=8192,\n    )\n\n    raw = response.choices[0].message.content or \"\"\n    tokens = response.usage.total_tokens if response.usage else 0\n    data = _parse_json_response(raw)\n    return _dict_to_result(data, \"deepseek\", model, tokens)"
```

**Edit 2 — Anthropic (`_call_anthropic`, ~line 231):**
```
old_string: "        model=model,\n        max_tokens=4096,\n        system=SYSTEM_PROMPT,"
new_string: "        model=model,\n        max_tokens=8192,\n        system=SYSTEM_PROMPT,"
```
(`system=SYSTEM_PROMPT` only appears in `_call_anthropic`)

**Edit 3 — OpenAI (`_call_openai`, ~line 270):**
```
old_string: "        temperature=0.1,\n        max_tokens=4096,\n    )\n\n    raw = response.choices[0].message.content or \"\"\n    tokens = response.usage.total_tokens if response.usage else 0\n    data = _parse_json_response(raw)\n    return _dict_to_result(data, \"openai\", model, tokens)"
new_string: "        temperature=0.05,\n        max_tokens=8192,\n    )\n\n    raw = response.choices[0].message.content or \"\"\n    tokens = response.usage.total_tokens if response.usage else 0\n    data = _parse_json_response(raw)\n    return _dict_to_result(data, \"openai\", model, tokens)"
```

- [ ] **Step 2: Commit**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && git add paperfraud/review/llm_review.py && git commit -m "feat: increase LLM max_tokens to 8192, lower temperature to 0.05

- Prevents output truncation for papers with many signals
- Improves output consistency across providers"
```

---

### Task 4: Adapt cli.py — Terminal Report for New Aggregated Fields

**Files:**
- Modify: `paperfraud/cli.py` (`_print_terminal_report` function, lines 373-485)

- [ ] **Step 1: Add risk_score display to terminal report**

Insert after the "综合判定" line and before the summary table in `_print_terminal_report`:

Find this block in `_print_terminal_report` (around line 394-396):
```python
    console.print(f"[bold {overall_color}]综合判定: {aggregated['overall_verdict']}[/bold {overall_color}]")
    console.print()
```

Insert risk score display after it:
```python
    # Risk Score bar
    risk_score = aggregated.get("risk_score", 0)
    risk_breakdown = aggregated.get("risk_breakdown", {})
    bar_width = 40
    filled = int(risk_score / 100 * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    score_color = "red" if risk_score >= 51 else "yellow" if risk_score >= 31 else "green"
    console.print(f"[bold {score_color}]欺诈风险评分: {risk_score:.0f}/100[/bold {score_color}]")
    console.print(f"[{score_color}]{bar}[/{score_color}]")
    if risk_breakdown.get("veto_triggered"):
        console.print("[red bold]⚠️ 一票否决触发：检测到数学铁证[/red bold]")
    if risk_breakdown.get("floor_applied"):
        console.print("[yellow dim]保底机制已激活：存在红色信号，分数已提升至最低阈值[/yellow dim]")
    if risk_breakdown.get("cluster_bonus", 0) > 0:
        console.print(f"[dim]聚类加分: +{risk_breakdown['cluster_bonus']}（多信号指向同一数据源）[/dim]")
    console.print()
```

- [ ] **Step 2: Adapt needs_human iteration**

The `needs_human` list changed from `list[CheckResult]` to `list[dict]`. Since `_print_terminal_report` only accesses `aggregated["needs_human_count"]` (not iterating the list), no changes needed. Verify:

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && grep -n "needs_human" paperfraud/cli.py
```

- [ ] **Step 3: Commit**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && git add paperfraud/cli.py && git commit -m "feat: add risk_score bar and breakdown to terminal report

- Visual 0-100 risk score bar with color coding
- Veto trigger warning display
- Floor activation notice
- Cluster bonus display"
```

---

### Task 5: Adapt formatter.py — JSON/Markdown for New Aggregated Fields

**Files:**
- Modify: `paperfraud/report/formatter.py`

- [ ] **Step 1: Add risk_score fields to format_json**

In `format_json`, add to the `summary` dict (after line 43):
```python
        "risk_score": round(aggregated.get("risk_score", 0), 1),
    }
    risk_breakdown = aggregated.get("risk_breakdown")
    if risk_breakdown:
        summary["risk_breakdown"] = risk_breakdown
```

The full change — insert between `"needs_human_count"` line and the closing `}`:
```python
    summary: dict[str, Any] = {
        "overall_level": aggregated["overall_level"],
        "overall_verdict": aggregated["overall_verdict"],
        "red_count": aggregated["red_count"],
        "orange_count": aggregated["orange_count"],
        "yellow_count": aggregated["yellow_count"],
        "green_count": aggregated["green_count"],
        "error_count": aggregated["error_count"],
        "total_checks": aggregated["total_checks"],
        "needs_human_count": aggregated["needs_human_count"],
        "risk_score": round(aggregated.get("risk_score", 0), 1),
    }
    risk_breakdown = aggregated.get("risk_breakdown")
    if risk_breakdown:
        summary["risk_breakdown"] = risk_breakdown
```

- [ ] **Step 2: Add risk_score section to format_markdown**

In `format_markdown`, after the summary table (after line 90), add:
```python
    # Risk Score
    risk_score = aggregated.get("risk_score", 0)
    lines.append(f"## 欺诈风险评分: {risk_score:.0f}/100")
    lines.append("")
    risk_breakdown = aggregated.get("risk_breakdown", {})
    if risk_breakdown.get("veto_triggered"):
        lines.append("⚠️ **一票否决触发：检测到数学铁证**")
        lines.append("")
    if risk_breakdown.get("cluster_bonus", 0) > 0:
        lines.append(f"聚类加分: +{risk_breakdown['cluster_bonus']}")
        lines.append("")
    contributions = risk_breakdown.get("contributions", [])
    if contributions:
        lines.append("| 检测项 | 级别 | 权重 | 相关性 | 贡献分 |")
        lines.append("|--------|------|------|--------|--------|")
        for c in contributions:
            lines.append(
                f"| {c['check_name']} | {c['level']} | {c['weight']:.1f} | "
                f"{c['correlation']:.1f} | {c['contribution']:.1f} |"
            )
        lines.append("")
```

- [ ] **Step 3: Verify formatter import doesn't break**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -c "
from paperfraud.report.formatter import format_json, format_markdown
from paperfraud.base import CheckResult
from paperfraud.report.aggregator import aggregate_results

results = [CheckResult(check_id='numbers.grim', check_name='GRIM', level='green', verdict='ok')]
agg = aggregate_results(results)
json_out = format_json(agg, results)
assert 'risk_score' in json_out
print('format_json: OK')

md_out = format_markdown(agg, results, title='Test')
assert '风险评分' in md_out
print('format_markdown: OK')
"
```

Expected: Both `OK` messages.

- [ ] **Step 4: Commit**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && git add paperfraud/report/formatter.py && git commit -m "feat: add risk_score and breakdown to JSON/Markdown reports

- JSON summary now includes risk_score and risk_breakdown
- Markdown report now includes risk score section with contribution table"
```

---

### Task 6: Integration Test — Full Pipeline on Test PDFs

**Files:**
- No file changes — verification only

- [ ] **Step 1: Run check on akkermansia.pdf (no images)**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m paperfraud.cli check "tests/fixtures/akkermansia.pdf" 2>&1 | head -60
```

Expected: Runs without errors. Output shows risk score bar. No `KeyError` or `AttributeError`.

- [ ] **Step 2: Run check on tveq.pdf (with images)**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m paperfraud.cli check "tests/fixtures/tveq.pdf" --extract-images --output-dir /tmp/tveq_test 2>&1 | head -80
```

Expected: Runs without errors. Images extracted. Report JSON saved.

- [ ] **Step 3: Verify report.json has new fields**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -c "
import json
with open('/tmp/tveq_test/report.json') as f:
    data = json.load(f)
summary = data['summary']
assert 'risk_score' in summary, 'missing risk_score'
assert 'risk_breakdown' in summary, 'missing risk_breakdown'
print(f'risk_score: {summary[\"risk_score\"]}')
print(f'overall_level: {summary[\"overall_level\"]}')
print(f'overall_verdict: {summary[\"overall_verdict\"]}')
print('Report JSON validation: OK')
"
```

Expected: Prints risk_score, level, verdict. `OK`.

- [ ] **Step 4: Run markdown output format**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m paperfraud.cli check "tests/fixtures/akkermansia.pdf" --output markdown 2>&1 | head -50
```

Expected: Markdown output includes "欺诈风险评分" section.

- [ ] **Step 5: Run JSON output format**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m paperfraud.cli check "tests/fixtures/akkermansia.pdf" --output json 2>&1 | python -m json.tool > /dev/null && echo "Valid JSON: OK"
```

Expected: `Valid JSON: OK`.

- [ ] **Step 6: Cleanup**

```bash
rm -rf /tmp/tveq_test
```

- [ ] **Step 7: Commit (if any fixes were needed)**

Only if integration tests revealed issues requiring code changes.

---

### Task 7: LLM Review Integration Test (Optional — requires API key)

**Files:**
- No file changes — verification only

- [ ] **Step 1: Run check + review on tveq.pdf**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -m paperfraud.cli check "tests/fixtures/tveq.pdf" --output-dir /tmp/tveq_review --review 2>&1
```

Expected (if `DEEPSEEK_API_KEY` is set):
- LLM review completes
- Terminal output shows "LLM 定性审查" section with signal_reviews table
- No JSON parse errors from LLM response
- `report.json` contains `llm_review` key with valid structure

If `DEEPSEEK_API_KEY` is not set, skip this task.

- [ ] **Step 2: Verify llm_review structure in JSON**

```bash
cd /Users/chaco/Desktop/paper fraud/paperfraud && PYTHONPATH="." python -c "
import json
with open('/tmp/tveq_review/report.json') as f:
    data = json.load(f)
review = data.get('llm_review', {})
assert 'overall_assessment' in review
assert 'severity_score' in review
assert 'signal_reviews' in review
assert isinstance(review['signal_reviews'], list)
for sr in review['signal_reviews']:
    assert 'check_id' in sr
    assert 'is_true_positive' in sr
    assert 'reasoning' in sr
    assert 'severity' in sr
print(f'severity_score: {review[\"severity_score\"]}')
print(f'signal_reviews count: {len(review[\"signal_reviews\"])}')
print('LLM review structure: OK')
"
```

- [ ] **Step 3: Cleanup**

```bash
rm -rf /tmp/tveq_review
```

---

## Verification Checklist

After all tasks complete, verify:

- [ ] `pytest tests/test_aggregator.py -v` — all 23 tests pass
- [ ] `paperfraud check tests/fixtures/akkermansia.pdf` — runs without errors, shows risk_score bar
- [ ] `paperfraud check tests/fixtures/tveq.pdf --extract-images --output-dir /tmp/test` — runs without errors
- [ ] JSON output includes `risk_score` and `risk_breakdown` fields
- [ ] Markdown output includes "欺诈风险评分" section
- [ ] Veto triggers when GRIM red + confidence ≥ 0.9
- [ ] Floor ensures single red → risk_score ≥ 51
- [ ] Few-shot examples use fictional data (not matching test PDFs)
- [ ] `--review` flag works if API key is set (optional)
