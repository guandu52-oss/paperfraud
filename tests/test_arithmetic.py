"""Unit tests for arithmetic relationship detection."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.numbers.arithmetic import (
    _is_valid_header,
    ColumnData,
    extract_table_columns,
    check_arithmetic_relationship,
    run_arithmetic_check,
)


class TestIsValidHeader:
    def test_too_short_rejected(self):
        assert _is_valid_header("AB") is False

    def test_no_alpha_rejected(self):
        assert _is_valid_header("123") is False

    def test_too_long_rejected(self):
        assert _is_valid_header("A" * 81) is False

    def test_valid_header_accepted(self):
        assert _is_valid_header("Control") is True

    def test_header_with_numbers_accepted(self):
        assert _is_valid_header("Group A") is True


class TestExtractTableColumns:
    def test_extracts_simple_columns(self):
        text = "Control 1.23 2.34 3.45\nTreatment 1.85 3.51 5.18"
        cols = extract_table_columns(text)
        assert len(cols) >= 1

    def test_empty_text_returns_empty(self):
        assert extract_table_columns("") == []


class TestCheckArithmeticRelationship:
    def test_exact_ratio_detected(self):
        col_a = ColumnData(header="Control", values=[1.0, 2.0, 3.0, 4.0, 5.0], n=5)
        col_b = ColumnData(header="Treatment", values=[1.5, 3.0, 4.5, 6.0, 7.5], n=5)
        result = check_arithmetic_relationship(col_a, col_b)
        assert result["is_suspicious"] is True
        assert abs(result["mean_ratio"] - 1.5) < 0.001
        assert result["cv"] < 0.01

    def test_natural_variation_not_suspicious(self):
        col_a = ColumnData(header="Control", values=[1.0, 2.0, 3.0, 4.0, 5.0], n=5)
        col_b = ColumnData(header="Treatment", values=[1.3, 2.8, 3.1, 5.5, 7.2], n=5)
        result = check_arithmetic_relationship(col_a, col_b)
        assert result["is_suspicious"] is False
        assert result["cv"] > 0.01

    def test_too_few_pairs(self):
        col_a = ColumnData(header="A", values=[1.0, 2.0], n=2)
        col_b = ColumnData(header="B", values=[1.5, 3.0], n=2)
        result = check_arithmetic_relationship(col_a, col_b)
        assert "error" in result

    def test_unequal_lengths_aligned(self):
        col_a = ColumnData(header="A", values=[1.0, 2.0, 3.0, 4.0], n=4)
        col_b = ColumnData(header="B", values=[2.0, 4.0, 6.0], n=3)
        result = check_arithmetic_relationship(col_a, col_b)
        assert result["n_pairs"] == 3
        assert result["is_suspicious"] is False  # n < 5

    def test_less_than_5_pairs_no_suspicious_flag(self):
        col_a = ColumnData(header="A", values=[1.0, 2.0, 3.0], n=3)
        col_b = ColumnData(header="B", values=[2.0, 4.0, 6.0], n=3)
        result = check_arithmetic_relationship(col_a, col_b)
        assert result["is_suspicious"] is False  # n < 5 even with CV < 1%


class TestRunArithmeticCheck:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_arithmetic_check(paper)
        assert results[0].level == "error"

    def test_no_columns_returns_green(self):
        paper = ParsedPaper(results="No numeric data.", full_text="No numeric data.")
        results = run_arithmetic_check(paper)
        assert results[0].level == "green"

    def test_single_column_returns_green(self):
        paper = ParsedPaper(
            results="Control 1.23 2.34 3.45",
            full_text="Control 1.23 2.34 3.45",
        )
        results = run_arithmetic_check(paper)
        assert results[0].level == "green"
