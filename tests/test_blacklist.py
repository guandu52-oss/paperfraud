"""Unit tests for blacklist word scanner."""
from __future__ import annotations

import pytest
from paperfraud.base import ParsedPaper
from paperfraud.checks.text.blacklist import scan_blacklist, run_blacklist


class TestScanBlacklist:
    def test_detects_novel(self):
        hits = scan_blacklist("This is a novel approach to the problem.")
        assert len(hits) >= 1
        assert any(h["term"].lower() == "novel" for h in hits)

    def test_detects_breakthrough(self):
        hits = scan_blacklist("Our breakthrough discovery changes everything.")
        assert len(hits) >= 1

    def test_detects_definitively(self):
        hits = scan_blacklist("This definitively proves the hypothesis.")
        assert len(hits) >= 1
        assert hits[0]["category"] == "绝对化表述"

    def test_normal_text_no_hits(self):
        hits = scan_blacklist(
            "We measured the concentration of protein in serum samples. "
            "The results were analyzed using standard statistical methods."
        )
        assert len(hits) == 0

    def test_empty_text(self):
        assert scan_blacklist("") == []

    def test_categorizes_correctly(self):
        hits = scan_blacklist("This novel breakthrough is remarkable and definitively proven.")
        categories = {h["category"] for h in hits}
        assert "过度宣称" in categories
        assert "夸大修饰" in categories


class TestRunBlacklist:
    def test_empty_text_returns_error(self):
        paper = ParsedPaper(full_text="")
        results = run_blacklist(paper)
        assert results[0].level == "error"

    def test_no_hits_returns_green(self):
        paper = ParsedPaper(
            discussion="Standard methods were used. Results showed expected patterns.",
            full_text="Standard methods were used. Results showed expected patterns.",
        )
        results = run_blacklist(paper)
        assert results[0].level == "green"

    def test_few_hits_returns_green(self):
        paper = ParsedPaper(
            discussion="We present a novel method for analysis.",
            full_text="We present a novel method for analysis.",
        )
        results = run_blacklist(paper)
        assert results[0].level == "green"

    def test_many_hits_returns_orange(self):
        paper = ParsedPaper(
            discussion=(
                "This novel, breakthrough, revolutionary approach is remarkable. "
                "Our excellent results are unprecedented and extraordinary. "
                "This definitively proves the outstanding finding."
            ),
            full_text=(
                "This novel, breakthrough, revolutionary approach is remarkable. "
                "Our excellent results are unprecedented and extraordinary. "
                "This definitively proves the outstanding finding."
            ),
        )
        results = run_blacklist(paper)
        assert results[0].level == "orange"
