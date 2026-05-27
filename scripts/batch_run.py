"""Batch runner — process all papers in a directory and rank by suspicion.

Supports: .pdf, .docx
Recursively scans subdirectories.
Auto-detects supplementary data files (CSV/TSV) in same directory.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure paperfraud is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperfraud.config import Config
from paperfraud.parser.engine import parse_paper
from paperfraud.report.aggregator import aggregate_results
from paperfraud.cli import _run_all_checks

SUPPORTED_SUFFIXES = {".pdf", ".docx"}


def _find_supplementary_data(paper_path: Path) -> str:
    """Scan paper's directory for CSV/TSV files to use for Benford/GRIM."""
    parent = paper_path.parent
    stem = paper_path.stem
    for ext in (".csv", ".tsv", ".txt"):
        candidate = parent / f"{stem}{ext}"
        if candidate.exists():
            return str(candidate)
    csv_files = list(parent.glob("*.csv")) + list(parent.glob("*.tsv"))
    return str(csv_files[0]) if csv_files else ""


def _collect_papers(paths: list[Path], formats: set[str]) -> list[Path]:
    """Collect paper paths from directories or individual files.

    Recursively scans directories for supported formats.
    """
    paper_paths: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() in formats:
            paper_paths.append(p)
        elif p.is_dir():
            for fmt in formats:
                paper_paths.extend(sorted(p.rglob(f"*{fmt}")))
                if fmt == ".pdf":
                    paper_paths.extend(sorted(p.rglob("*.PDF")))

    # Deduplicate by resolved path
    seen = set()
    unique = []
    for p in paper_paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def process_paper(paper_path: Path, config: Config) -> dict:
    """Process a single paper and return {path, summary, error}."""
    start = time.time()
    try:
        paper = parse_paper(paper_path, config)
        results = _run_all_checks(paper, config)
        aggregated = aggregate_results(results)

        title = paper.title or paper_path.stem
        elapsed = time.time() - start

        return {
            "path": str(paper_path),
            "title": title,
            "journal": paper.journal or "",
            "year": paper.year or "",
            "authors": paper.authors[:3] if paper.authors else [],
            "format": paper_path.suffix.lower(),
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
            "path": str(paper_path),
            "title": paper_path.stem,
            "journal": "",
            "year": "",
            "authors": [],
            "format": paper_path.suffix.lower(),
            "summary": None,
            "red_signals": [],
            "elapsed_seconds": round(time.time() - start, 1),
            "error": str(e),
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Batch paperfraud on all papers in directories")
    parser.add_argument("dirs", nargs="+", help="Directories or individual paper paths")
    parser.add_argument("--output", "-o", default="batch_results.json", help="Output JSON file")
    parser.add_argument("--extract-images", action="store_true", help="Enable image forensics (slow)")
    parser.add_argument("--max-papers", type=int, default=0, help="Max papers to process (0=all)")
    parser.add_argument("--data-file", help="CSV for Benford/GRIM (applies to all papers)")
    parser.add_argument("--no-external", action="store_true", default=True, help="Disable external requests")
    parser.add_argument("--formats", nargs="+", default=["pdf", "docx"],
                        help="File formats to process (default: pdf docx)")
    args = parser.parse_args()

    # Map format names to suffixes
    format_map = {"pdf": ".pdf", "docx": ".docx"}
    formats = set()
    for f in args.formats:
        if f in format_map:
            formats.add(format_map[f])
        elif f.startswith("."):
            formats.add(f.lower())
    if not formats:
        formats = SUPPORTED_SUFFIXES

    # Collect paper paths
    paths = [Path(p) for p in args.dirs]
    paper_paths = _collect_papers(paths, formats)

    if args.max_papers > 0:
        paper_paths = paper_paths[:args.max_papers]

    print(f"共发现 {len(paper_paths)} 篇论文")
    if formats != SUPPORTED_SUFFIXES:
        fmt_names = ", ".join(sorted(formats))
        print(f"格式过滤: {fmt_names}")
    print("=" * 60)

    config = Config(
        skip_images=not args.extract_images,
        no_external=args.no_external,
        data_file=args.data_file or "",
    )

    results = []
    for i, paper_path in enumerate(paper_paths, 1):
        print(f"\n[{i}/{len(paper_paths)}] {paper_path.name}")

        # Auto-detect supplementary data
        if not config.data_file:
            auto_data = _find_supplementary_data(paper_path)
            if auto_data:
                config.data_file = auto_data
                print(f"  📎 关联数据: {Path(auto_data).name}")

        result = process_paper(paper_path, config)
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
        fmt_tag = f" [{r.get('format', '')}]" if r.get('format') else ""
        print(f"\n{rank:2d}. {flag} {r['title'][:70]}{fmt_tag}")
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
