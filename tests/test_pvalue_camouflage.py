"""Unit tests for P-value camouflage detection."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.text.pvalue_camouflage import scan_camouflage, run_pvalue_camouflage


class TestScanCamouflage:
    def test_detects_marginally_significant(self):
        hits = scan_camouflage("The result was marginally significant (p = 0.06).")
        assert len(hits) >= 1
        assert "marginally" in hits[0]["phrase"].lower()

    def test_detects_trending_towards(self):
        hits = scan_camouflage("The data was trending towards significance.")
        assert len(hits) >= 1

    def test_detects_approached_significance(self):
        hits = scan_camouflage("The difference approached statistical significance.")
        assert len(hits) >= 1

    def test_detects_borderline_significant(self):
        hits = scan_camouflage("The effect was borderline significant.")
        assert len(hits) >= 1

    def test_detects_nominally_significant(self):
        hits = scan_camouflage("The nominally significant association was noted.")
        assert len(hits) >= 1

    def test_detects_almost_significant(self):
        hits = scan_camouflage("The treatment almost reached significance.")
        assert len(hits) >= 1

    def test_normal_text_no_hits(self):
        hits = scan_camouflage(
            "The treatment group showed a statistically significant improvement "
            "(p = 0.03) compared to the control group."
        )
        assert len(hits) == 0

    def test_empty_text(self):
        assert scan_camouflage("") == []


class TestRunPvalueCamouflage:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_pvalue_camouflage(paper)
        assert results[0].level == "error"

    def test_no_hits_returns_green(self):
        paper = ParsedPaper(
            results="The treatment was significant (p = 0.03).",
            full_text="The treatment was significant (p = 0.03).",
        )
        results = run_pvalue_camouflage(paper)
        assert results[0].level == "green"

    def test_single_hit_returns_orange(self):
        paper = ParsedPaper(
            results="The result was marginally significant.",
            full_text="The result was marginally significant.",
        )
        results = run_pvalue_camouflage(paper)
        assert results[0].level == "orange"

    def test_multiple_hits_returns_red(self):
        paper = ParsedPaper(
            results=(
                "The data was trending towards significance. "
                "The difference approached significance. "
                "The effect was borderline significant."
            ),
            discussion=(
                "The data was trending towards significance. "
                "The difference approached significance. "
                "The effect was borderline significant."
            ),
            full_text=(
                "The data was trending towards significance. "
                "The difference approached significance. "
                "The effect was borderline significant."
            ),
        )
        results = run_pvalue_camouflage(paper)
        assert results[0].level == "red"
