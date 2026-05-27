"""Unit tests for identical value detection."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.numbers.identical_values import (
    extract_values_with_context,
    detect_identical_values,
    _is_pvalue_threshold,
    run_identical_values,
)


class TestIsPvalueThreshold:
    def test_common_thresholds_detected(self):
        assert _is_pvalue_threshold(0.05, "p < 0.05 was considered significant") is True
        assert _is_pvalue_threshold(0.01, "significance level") is True
        assert _is_pvalue_threshold(0.001, "") is True

    def test_data_value_not_threshold(self):
        assert _is_pvalue_threshold(0.042, "the treatment group") is False

    def test_near_pvalue_context(self):
        assert _is_pvalue_threshold(0.03, "P > 0.03 for all groups") is True


class TestExtractValuesWithContext:
    def test_extracts_decimal_numbers(self):
        vals = extract_values_with_context("Groups: 1.23, 2.34, 3.45")
        assert len(vals) == 3

    def test_skips_years(self):
        vals = extract_values_with_context("In 2023, the data showed 1.23")
        assert len(vals) == 1
        assert vals[0]["value"] == 1.23

    def test_empty_text(self):
        assert extract_values_with_context("") == []


class TestDetectIdenticalValues:
    def test_unique_values_no_duplicates(self):
        vals = extract_values_with_context(
            "Control: weight 1.23, height 2.34. "
            "Treatment: weight 3.45, height 4.56."
        )
        result = detect_identical_values(vals)
        assert result["total_duplicate_values"] == 0


    def test_pvalue_thresholds_filtered(self):
        vals = extract_values_with_context(
            "p < 0.05 for group A. p < 0.05 for group B. p < 0.05 for group C."
        )
        result = detect_identical_values(vals)
        assert result["pvalue_filtered"] >= 3


class TestRunIdenticalValues:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_identical_values(paper)
        assert results[0].level == "error"

    def test_too_few_values_returns_error(self):
        paper = ParsedPaper(results="1.2 3.4", full_text="1.2 3.4")
        results = run_identical_values(paper)
        assert results[0].level == "error"

    def test_no_duplicates_returns_green(self):
        paper = ParsedPaper(
            results=" ".join(f"Group {i}: {i}.{j}" for i in range(5) for j in range(10, 20)),
            full_text=" ".join(f"Group {i}: {i}.{j}" for i in range(5) for j in range(10, 20)),
        )
        results = run_identical_values(paper)
        assert results[0].level == "green"
