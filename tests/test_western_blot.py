"""Unit tests for Western blot loading control checker."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.bioinfo.western_blot import run_western_blot_check


class TestRunWesternBlotCheck:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_western_blot_check(paper)
        assert results[0].level == "error"

    def test_no_wb_mentioned_returns_green(self):
        paper = ParsedPaper(
            methods="qPCR was performed using SYBR Green.",
            full_text="qPCR was performed using SYBR Green.",
        )
        results = run_western_blot_check(paper)
        assert results[0].level == "green"

    def test_wb_with_loading_control_returns_green(self):
        paper = ParsedPaper(
            methods=(
                "Western blot was performed using standard protocols. "
                "GAPDH was used as a loading control."
            ),
            full_text=(
                "Western blot analysis revealed protein expression changes. "
                "GAPDH was used as a loading control."
            ),
        )
        results = run_western_blot_check(paper)
        assert results[0].level == "green"

    def test_wb_with_beta_actin_returns_green(self):
        paper = ParsedPaper(
            methods=(
                "Immunoblot analysis was performed. "
                "β-actin served as the internal control."
            ),
            full_text=(
                "Immunoblot analysis was performed. "
                "β-actin served as the internal control."
            ),
        )
        results = run_western_blot_check(paper)
        assert results[0].level == "green"

    def test_wb_without_loading_control_returns_red(self):
        paper = ParsedPaper(
            methods=(
                "Western blot was performed. Proteins were transferred "
                "to PVDF membrane and detected with ECL."
            ),
            full_text=(
                "Western blot analysis showed significant differences. "
                "Proteins were transferred to PVDF membrane and detected with ECL."
            ),
        )
        results = run_western_blot_check(paper)
        assert results[0].level == "red"
        assert results[0].needs_human is True

    def test_wb_with_total_protein_norm_returns_green(self):
        paper = ParsedPaper(
            methods=(
                "Western blot was performed. "
                "Total protein staining with Ponceau S was used for normalization."
            ),
            full_text=(
                "Western blot was performed. "
                "Total protein staining with Ponceau S was used for normalization."
            ),
        )
        results = run_western_blot_check(paper)
        assert results[0].level == "green"
