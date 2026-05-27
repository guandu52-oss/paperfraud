"""Unit tests for p-hacking detection."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.stats.p_hacking import run_p_hacking_check


class TestRunPHackingCheck:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_p_hacking_check(paper)
        assert results[0].level == "error"

    def test_too_few_pvalues_returns_green(self):
        paper = ParsedPaper(full_text="p = 0.03")
        results = run_p_hacking_check(paper)
        assert results[0].level == "green"

    def test_normal_distribution_returns_green(self):
        text = " ".join(
            f"t(20) = {t:.1f}, p = {p:.3f}"
            for t, p in [
                (0.5, 0.62), (1.0, 0.33), (1.5, 0.15),
                (2.0, 0.06), (2.5, 0.02), (3.0, 0.007),
                (0.8, 0.43), (1.2, 0.24),
            ]
        )
        paper = ParsedPaper(full_text=text)
        results = run_p_hacking_check(paper)
        assert results[0].level == "green"

    def test_clustered_near_05_returns_red(self):
        text = " ".join(f"t(20) = 2.1, p = {p:.4f}" for p in [
            0.041, 0.042, 0.043, 0.044, 0.045, 0.046, 0.047, 0.048, 0.049, 0.049
        ])
        paper = ParsedPaper(full_text=text)
        results = run_p_hacking_check(paper)
        assert results[0].level in ("red", "yellow")

    def test_all_inequality_no_exact(self):
        text = " ".join("p < 0.05" for _ in range(10))
        paper = ParsedPaper(full_text=text)
        results = run_p_hacking_check(paper)
        assert results[0].level in ("yellow", "green")
