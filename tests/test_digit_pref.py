"""Unit tests for digit preference and decimal consistency."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.numbers.digit_pref import (
    extract_numeric_values,
    check_digit_preference,
    check_decimal_consistency,
    run_digit_checks,
)


class TestExtractNumericValues:
    def test_extracts_decimals(self):
        vals = extract_numeric_values("Values: 1.23, 2.34, 3.45")
        assert len(vals) == 3
        assert 1.23 in vals

    def test_skips_years(self):
        vals = extract_numeric_values("Year 2023, value 1.23, year 1999")
        assert 2023.0 not in vals
        assert 1999.0 not in vals
        assert 1.23 in vals

    def test_skips_section_labels(self):
        vals = extract_numeric_values("Section 1.2.3.4, value 5.67")
        assert 5.67 in vals

    def test_empty_text(self):
        assert extract_numeric_values("") == []


class TestCheckDigitPreference:
    def test_uniform_distribution_not_suspicious(self):
        values = [float(f"{i}.{j}") for i in range(4) for j in range(10)]
        result = check_digit_preference(values)
        assert result["is_suspicious"] is False

    def test_biased_to_zero_and_five(self):
        values = (
            [1.0, 2.0, 3.0, 4.0, 5.0] * 4
            + [1.5, 2.5, 3.5, 4.5, 5.5] * 4
        )
        result = check_digit_preference(values)
        assert result["zero_five_pct"] > 35

    def test_insufficient_sample(self):
        result = check_digit_preference([1.1, 2.2, 3.3])
        assert result["p_value"] is None
        assert result["is_suspicious"] is False


class TestCheckDecimalConsistency:
    def test_consistent_precision(self):
        vals = [1.23, 2.34, 3.45, 4.56]
        result = check_decimal_consistency(vals)
        assert result["consistent"] is True

    def test_mixed_precision(self):
        vals = [1.2, 2.34, 3.456, 4.5, 5.678]
        result = check_decimal_consistency(vals)
        assert result["consistent"] is False

    def test_empty_list(self):
        result = check_decimal_consistency([])
        assert result["consistent"] is True


class TestRunDigitChecks:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_digit_checks(paper)
        assert results[0].level == "error"

    def test_too_few_values_returns_error(self):
        paper = ParsedPaper(results="1.2 3.4", full_text="1.2 3.4")
        results = run_digit_checks(paper)
        assert results[0].level == "error"

    def test_normal_values_returns_green(self):
        vals = " ".join(f"{i}.{j}" for i in range(3) for j in range(10))
        paper = ParsedPaper(results=vals, full_text=vals)
        results = run_digit_checks(paper)
        assert results[0].level == "green"
