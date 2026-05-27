"""Unit tests for py-statcheck (P-value recalculation)."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.numbers.py_statcheck import (
    extract_stats,
    recalculate_p,
    flag_inconsistency,
    run_py_statcheck,
    StatResult,
)


class TestExtractStats:
    def test_extracts_t_test(self):
        stats = extract_stats("t(22) = 3.10, p = 0.005")
        assert len(stats) >= 1
        s = stats[0]
        assert s.test_type == "t"
        assert s.df1 == 22.0
        assert s.reported_value == 3.10
        assert s.reported_p == 0.005

    def test_extracts_f_test(self):
        stats = extract_stats("F(1, 28) = 5.32, p = 0.029")
        assert len(stats) >= 1
        s = stats[0]
        assert s.test_type == "F"
        assert s.df1 == 1.0
        assert s.df2 == 28.0
        assert s.reported_value == 5.32

    def test_extracts_chisq(self):
        stats = extract_stats("chi square(1, N=100) = 4.51, p = 0.034")
        assert len(stats) >= 1
        s = stats[0]
        assert s.test_type == "chisq"
        assert s.df1 == 1.0

    def test_extracts_unicode_chisq(self):
        stats = extract_stats("χ²(1) = 4.51, p = 0.034")
        assert len(stats) >= 1
        assert stats[0].test_type == "chisq"

    def test_extracts_r_test(self):
        stats = extract_stats("r(30) = 0.45, p = 0.012")
        assert len(stats) >= 1
        s = stats[0]
        assert s.test_type == "r"
        assert s.df1 == 30.0

    def test_extracts_z_test(self):
        stats = extract_stats("Z = 2.34, p = 0.019")
        assert len(stats) >= 1
        s = stats[0]
        assert s.test_type == "Z"
        assert s.reported_value == 2.34

    def test_extracts_multiple_stats(self):
        text = (
            "t(22) = 3.10, p = 0.005; "
            "F(1, 28) = 5.32, p = 0.029; "
            "chi-square(2) = 8.21, p = 0.016"
        )
        stats = extract_stats(text)
        types = [s.test_type for s in stats]
        assert "t" in types
        assert "F" in types

    def test_no_stats_returns_empty(self):
        assert extract_stats("No statistics here.") == []

    def test_extracts_p_inequality(self):
        stats = extract_stats("t(15) = 2.13, p < 0.05")
        assert len(stats) >= 1
        s = stats[0]
        assert s.reported_p == 0.05
        assert s.p_comparison == "<"

    def test_no_p_value_field(self):
        stats = extract_stats("t(10) = 1.50")
        assert len(stats) >= 1
        assert stats[0].reported_p is None


class TestRecalculateP:
    def test_t_test_recalculation(self):
        r = StatResult(test_type="t", df1=22, reported_value=3.10)
        recalculate_p(r)
        assert r.recalculated_p is not None
        assert 0.004 < r.recalculated_p < 0.006

    def test_f_test_recalculation(self):
        r = StatResult(test_type="F", df1=1, df2=28, reported_value=5.32)
        recalculate_p(r)
        assert r.recalculated_p is not None
        assert 0.02 < r.recalculated_p < 0.04

    def test_chisq_recalculation(self):
        r = StatResult(test_type="chisq", df1=1, reported_value=4.51)
        recalculate_p(r)
        assert r.recalculated_p is not None
        assert 0.03 < r.recalculated_p < 0.04

    def test_z_recalculation(self):
        r = StatResult(test_type="Z", reported_value=1.96)
        recalculate_p(r)
        assert r.recalculated_p is not None
        assert abs(r.recalculated_p - 0.05) < 0.001

    def test_r_recalculation(self):
        r = StatResult(test_type="r", df1=30, reported_value=0.45)
        recalculate_p(r)
        assert r.recalculated_p is not None
        assert 0.009 < r.recalculated_p < 0.011

    def test_missing_value_no_calc(self):
        r = StatResult(test_type="t", df1=10, reported_value=None)
        recalculate_p(r)
        assert r.recalculated_p is None

    def test_r_equals_1_no_calc(self):
        r = StatResult(test_type="r", df1=10, reported_value=1.0)
        recalculate_p(r)
        assert r.recalculated_p is None


class TestFlagInconsistency:
    def test_decision_error_sig_vs_nonsig(self):
        r = StatResult(
            test_type="t", p_comparison="<", reported_p=0.05, recalculated_p=0.12
        )
        flag_inconsistency(r)
        assert r.is_error is True
        assert r.error_type == "decision_error"

    def test_gross_mismatch_same_direction(self):
        r = StatResult(
            test_type="t", p_comparison="=", reported_p=0.04, recalculated_p=0.00005
        )
        flag_inconsistency(r)
        assert r.is_error is True
        assert r.error_type == "gross_mismatch"

    def test_decision_error_overrides_gross_mismatch(self):
        r = StatResult(
            test_type="t", p_comparison="=", reported_p=0.001, recalculated_p=0.10
        )
        flag_inconsistency(r)
        assert r.is_error is True
        assert r.error_type == "decision_error"

    def test_consistent_no_error(self):
        r = StatResult(
            test_type="t", p_comparison="=", reported_p=0.03, recalculated_p=0.032
        )
        flag_inconsistency(r)
        assert r.is_error is False

    def test_missing_values_no_error(self):
        r = StatResult(test_type="t", reported_p=None, recalculated_p=0.05)
        flag_inconsistency(r)
        assert r.is_error is False


class TestRunPyStatcheck:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_py_statcheck(paper)
        assert results[0].level == "error"

    def test_no_stats_returns_green(self):
        paper = ParsedPaper(results="No stats.", full_text="No stats.")
        results = run_py_statcheck(paper)
        assert results[0].level == "green"

    def test_all_consistent_returns_green(self):
        paper = ParsedPaper(
            results="t(22) = 3.10, p = 0.005; F(1, 28) = 5.32, p = 0.029",
            full_text="t(22) = 3.10, p = 0.005; F(1, 28) = 5.32, p = 0.029",
        )
        results = run_py_statcheck(paper)
        assert results[0].level == "green"

    def test_inconsistent_returns_red(self):
        paper = ParsedPaper(
            results="t(22) = 3.10, p = 0.830",
            full_text="t(22) = 3.10, p = 0.830",
        )
        results = run_py_statcheck(paper)
        assert results[0].level == "red"
