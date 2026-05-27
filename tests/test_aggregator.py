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
    aggregate_results,
)


class TestTokenize:
    def test_extracts_numbers_and_terms(self):
        tokens = _tokenize("n=3 mice per group, p<0.001 with SD=0.42")
        assert "0.001" in tokens
        assert "mice" in tokens
        assert "group" in tokens
        assert "0.42" in tokens

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
        assert result == 2 / 4

    def test_empty_input(self):
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0


class TestVetoOverride:
    def test_grim_red_high_confidence_triggers_veto(self):
        results = [
            CheckResult(
                check_id="numbers.grim", check_name="GRIM",
                level="red", verdict="GRIM inconsistent",
                confidence=0.95,
                source_locations=[
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
                confidence=0.8,
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
        expected = 25 * 1.0 * 1.0
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
        assert breakdown["cluster_bonus"] == 10
        assert score > 25 * 3

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
        expected = 12 * 0.5 * 1.0 + 5 * 0.3 * 1.0
        assert score == expected


class TestFloor:
    def test_red_floor_minimum_51(self):
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
        assert ranked[0]["check_id"] == "numbers.grim"
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
