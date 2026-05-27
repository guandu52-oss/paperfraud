"""Unit tests for sample size checker."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.stats.sample_size import (
    _compute_p_table,
    _format_p,
    _min_d_for_significance,
    _detect_experiment_types,
    _build_claim_compact,
    run_sample_size_check,
)


class TestMinDForSignificance:
    def test_n12_alpha_05(self):
        d = _min_d_for_significance(12, 0.05)
        assert 0.8 < d < 0.9

    def test_n3_alpha_05(self):
        d = _min_d_for_significance(3, 0.05)
        assert d > 1.5

    def test_smaller_n_needs_larger_d(self):
        d_small = _min_d_for_significance(3, 0.05)
        d_large = _min_d_for_significance(12, 0.05)
        assert d_small > d_large

    def test_stricter_alpha_needs_larger_d(self):
        d_05 = _min_d_for_significance(10, 0.05)
        d_001 = _min_d_for_significance(10, 0.001)
        assert d_001 > d_05

    def test_n_less_than_3_returns_inf(self):
        d = _min_d_for_significance(1, 0.05)
        assert d == float("inf")


class TestComputePTable:
    def test_n3_produces_table(self):
        table = _compute_p_table(3)
        assert len(table) == 6
        labels = [r["label"] for r in table]
        assert "小效应" in labels
        assert "极巨大效应" in labels

    def test_n1_returns_empty(self):
        assert _compute_p_table(1) == []

    def test_larger_n_more_significant(self):
        small = _compute_p_table(5)
        large = _compute_p_table(20)
        small_sig = sum(1 for r in small if r["sig_05"])
        large_sig = sum(1 for r in large if r["sig_05"])
        assert large_sig >= small_sig


class TestFormatP:
    def test_very_small(self):
        assert _format_p(0.00001) == "p < 0.0001"

    def test_small(self):
        assert "0.003" in _format_p(0.003)

    def test_large(self):
        assert "0.150" in _format_p(0.15)


class TestDetectExperimentTypes:
    def test_in_vitro(self):
        types = _detect_experiment_types("HEK293 cells were cultured in vitro")
        assert "细胞/分子实验 (in vitro)" in types

    def test_in_vivo(self):
        types = _detect_experiment_types("C57BL/6 mice were used")
        assert "动物实验 (in vivo)" in types

    def test_clinical(self):
        types = _detect_experiment_types("patients were enrolled in the trial")
        assert "临床/人体研究" in types

    def test_unknown(self):
        assert _detect_experiment_types("") == ["未知实验类型"]


class TestBuildClaimCompact:
    def test_produces_compact_output(self):
        lines = _build_claim_compact(5, ["动物实验 (in vivo)"])
        assert len(lines) >= 3


class TestRunSampleSizeCheck:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_sample_size_check(paper)
        assert results[0].level == "error"

    def test_no_sample_size_returns_green(self):
        paper = ParsedPaper(
            methods="Standard protocols were followed.",
            full_text="Results showed significant improvement.",
        )
        results = run_sample_size_check(paper)
        assert results[0].level == "green"

    def test_methods_n_lt_3_returns_red(self):
        paper = ParsedPaper(
            methods="n = 2 mice per group were used.",
            full_text="n = 2 mice per group were used.",
        )
        results = run_sample_size_check(paper)
        assert results[0].level == "red"

    def test_methods_n_ge_3_healthy(self):
        paper = ParsedPaper(
            methods="n = 15 mice per group, power analysis was performed using G*Power.",
            full_text="n = 15 mice per group. t(28) = 3.1, p = 0.005.",
        )
        results = run_sample_size_check(paper)
        assert results[0].level == "green"

    def test_technical_replicates_detected(self):
        paper = ParsedPaper(
            methods="Each experiment was performed in triplicate.",
            full_text="Each experiment was performed in triplicate. n = 3.",
        )
        results = run_sample_size_check(paper)
        verdict = results[0].verdict
        assert "伪重复" in verdict or "技术重复" in verdict
