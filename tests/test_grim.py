"""Unit tests for GRIM test."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.numbers.grim import grim_test, extract_mean_n_pairs, run_grim


class TestGrimTest:
    def test_consistent_integer_product(self):
        result = grim_test(mean=3.5, n=12)
        assert result["consistent"] is True
        assert result["product"] == 42.0
        assert result["nearest_int"] == 42
        assert result["error"] == 0.0

    def test_inconsistent_non_integer_product(self):
        result = grim_test(mean=3.47, n=12)
        assert result["consistent"] is False
        assert abs(result["product"] - 41.64) < 0.001
        assert result["nearest_int"] == 42
        assert abs(result["error"] - 0.36) < 0.01

    def test_consistent_with_small_n(self):
        result = grim_test(mean=5.0, n=2)
        assert result["consistent"] is True

    def test_inconsistent_with_odd_n(self):
        result = grim_test(mean=3.5, n=11)
        assert result["consistent"] is False

    def test_tolerance_scales_with_n(self):
        r_small = grim_test(mean=3.501, n=3)
        r_large = grim_test(mean=3.5001, n=100)
        assert r_small["tolerance"] < r_large["tolerance"]

    def test_custom_tolerance(self):
        result = grim_test(mean=3.47, n=12, tolerance=1.0)
        assert result["consistent"] is True

    def test_integer_n1_consistent(self):
        result = grim_test(mean=5.0, n=1)
        assert result["consistent"] is True


class TestExtractMeanNPairs:
    def test_extracts_standard_apa(self):
        text = "The treatment group showed M = 4.37 ± 0.82 (n = 12)."
        pairs = extract_mean_n_pairs(text)
        assert len(pairs) >= 1
        m = pairs[0]
        assert m["mean"] == 4.37
        assert m["n"] == 12
        assert m["sd"] == 0.82

    def test_extracts_pm_format(self):
        text = "Body weight was 25.30 ± 2.15 g (n = 8) after treatment."
        pairs = extract_mean_n_pairs(text)
        assert len(pairs) >= 1
        assert pairs[0]["mean"] == 25.30
        assert pairs[0]["n"] == 8

    def test_extracts_n_then_mean(self):
        text = "n = 20, mean = 12.50 for the control group."
        pairs = extract_mean_n_pairs(text)
        assert len(pairs) >= 1
        assert pairs[0]["mean"] == 12.50
        assert pairs[0]["n"] == 20

    def test_handles_no_pairs(self):
        assert extract_mean_n_pairs("No statistics here.") == []

    def test_extracts_multiple_pairs(self):
        text = (
            "Control: M = 10.50 ± 1.10 (n=6). "
            "Treatment: M = 15.30 ± 2.40 (n=6)."
        )
        pairs = extract_mean_n_pairs(text)
        assert len(pairs) >= 2


class TestRunGrim:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_grim(paper)
        assert len(results) == 1
        assert results[0].level == "error"

    def test_no_pairs_returns_green(self):
        paper = ParsedPaper(results="No data here.", full_text="No data here.")
        results = run_grim(paper)
        assert results[0].level == "green"

    def test_consistent_pairs_returns_green(self):
        paper = ParsedPaper(
            results="M = 3.50 ± 0.50 (n=12), M = 5.00 ± 0.80 (n=20)",
            full_text="M = 3.50 ± 0.50 (n=12), M = 5.00 ± 0.80 (n=20)",
        )
        results = run_grim(paper)
        assert results[0].level == "green"

    def test_inconsistent_pair_returns_red(self):
        paper = ParsedPaper(
            results="M = 4.37 ± 0.82, n = 12",
            full_text="M = 4.37 ± 0.82, n = 12",
        )
        results = run_grim(paper)
        assert results[0].level == "red"
        assert results[0].needs_human is True

    def test_falls_back_to_full_text(self):
        paper = ParsedPaper(
            results="",
            full_text="M = 3.50 ± 0.42 (n=6)",
        )
        results = run_grim(paper)
        assert results[0].level == "green"
