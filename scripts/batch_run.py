"""Batch runner — process all PDFs in a directory and rank by suspicion."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure paperfraud is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperfraud.config import Config
from paperfraud.parser.engine import parse_pdf
from paperfraud.report.aggregator import aggregate_results
from paperfraud.cli import _run_all_checks


def process_pdf(pdf_path: Path, config: Config) -> dict:
    """Process a single PDF and return {path, summary, error}."""
    start = time.time()
    try:
        paper = parse_pdf(pdf_path, config)
        results = _run_all_checks(paper, config)
        aggregated = aggregate_results(results)

        title = paper.title or pdf_path.stem
        elapsed = time.time() - start

        return {
            "path": str(pdf_path),
            "title": title,
            "journal": paper.journal or "",
            "year": paper.year or "",
            "authors": paper.authors[:3] if paper.authors else [],
            "summary": {
                "overall_level": aggregated["overall_level"],
                "overall_verdict": aggregated["overall_verdict"],
                "red_count": aggregated["red_count"],
                "orange_count": aggregated["orange_count"],
                "yellow_count": aggregated["yellow_count"],
                "green_count": aggregated["green_count"],
                "error_count": aggregated["error_count"],
                "total_checks": aggregated["total_checks"],
                "needs_human_count": aggregated["needs_human_count"],
            },
            "red_signals": [
                {"check_id": r.check_id, "check_name": r.check_name, "verdict": r.verdict}
                for r in results if r.level == "red"
            ],
            "elapsed_seconds": round(elapsed, 1),
            "error": None,
        }
    except Exception as e:
        return {
            "path": str(pdf_path),
            "title": pdf_path.stem,
            "journal": "",
            "year": "",
            "authors": [],
            "summary": None,
            "red_signals": [],
            "elapsed_seconds": round(time.time() - start, 1),
            "error": str(e),
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Batch paperfraud on all PDFs in directories")
    parser.add_argument("dirs", nargs="+", help="Directories or individual PDF paths")
    parser.add_argument("--output", "-o", default="batch_results.json", help="Output JSON file")
    parser.add_argument("--extract-images", action="store_true", help="Enable image forensics (slow)")
    parser.add_argument("--max-papers", type=int, default=0, help="Max papers to process (0=all)")
    parser.add_argument("--data-file", help="CSV for Benford/GRIM (applies to all papers)")
    parser.add_argument("--no-external", action="store_true", default=True, help="Disable external requests")
    args = parser.parse_args()

    # Collect PDF paths
    pdf_paths = []
    for path_str in args.dirs:
        p = Path(path_str)
        if p.is_file() and p.suffix.lower() == ".pdf":
            pdf_paths.append(p)
        elif p.is_dir():
            pdf_paths.extend(sorted(p.glob("*.pdf")))
            pdf_paths.extend(sorted(p.glob("*.PDF")))

    # Deduplicate
    seen = set()
    unique = []
    for p in pdf_paths:
        if p.name not in seen:
            seen.add(p.name)
            unique.append(p)
    pdf_paths = unique

    if args.max_papers > 0:
        pdf_paths = pdf_paths[:args.max_papers]

    print(f"共发现 {len(pdf_paths)} 篇 PDF")
    print("=" * 60)

    config = Config(
        skip_images=not args.extract_images,
        no_external=args.no_external,
        data_file=args.data_file or "",
    )

    results = []
    for i, pdf_path in enumerate(pdf_paths, 1):
        print(f"\n[{i}/{len(pdf_paths)}] {pdf_path.name}")
        result = process_pdf(pdf_path, config)
        results.append(result)

        if result["error"]:
            print(f"  ❌ 错误: {result['error']}")
        elif result["summary"]:
            s = result["summary"]
            print(f"  {result['title'][:80]}")
            flag = "🔴" if s["red_count"] > 0 else "🟠" if s["orange_count"] > 0 else "🟡" if s["yellow_count"] > 0 else "🟢"
            print(f"  {flag} Red:{s['red_count']} Orange:{s['orange_count']} Yellow:{s['yellow_count']} Green:{s['green_count']} Error:{s['error_count']} ({result['elapsed_seconds']}s)")

    # Rank by suspicion
    def _rank_key(r):
        if r["error"] or r["summary"] is None:
            return (-1, 0, 0)
        s = r["summary"]
        return (s["red_count"], s["orange_count"], s["yellow_count"])

    results.sort(key=_rank_key, reverse=True)

    # Print leaderboard
    print("\n" + "=" * 60)
    print("嫌疑度排行榜")
    print("=" * 60)

    for rank, r in enumerate(results, 1):
        if r["error"]:
            print(f"\n{rank:2d}. ❌ {r['title'][:70]} — 解析失败: {r['error']}")
            continue

        s = r["summary"]
        flag = "🔴" if s["red_count"] > 0 else "🟠" if s["orange_count"] > 0 else "🟡" if s["yellow_count"] > 0 else "🟢"
        print(f"\n{rank:2d}. {flag} {r['title'][:70]}")
        print(f"    Red:{s['red_count']} Orange:{s['orange_count']} Yellow:{s['yellow_count']} Green:{s['green_count']} Error:{s['error_count']} | {r.get('elapsed_seconds', 0)}s")
        if r["red_signals"]:
            for sig in r["red_signals"][:3]:
                print(f"      [RED] {sig['check_name']}: {sig['verdict'][:80]}")
        if r.get("journal"):
            print(f"    {r['journal']} ({r.get('year', '')})")

    # Save
    output = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_papers": len(results),
        "ranked": results,
    }
    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已保存到 {args.output}")


if __name__ == "__main__":
    main()
