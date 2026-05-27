"""CLI entry point for paperfraud-detect.

Usage:
    paperfraud 10.1038/s41586-2024-xxxxx
    paperfraud /path/to/paper.pdf
    paperfraud --output json paper.pdf
    paperfraud check paper.pdf --extract-images --web
    paperfraud serve paperfraud_output/<paper>/report.json
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from paperfraud import __version__
from paperfraud.base import CheckResult
from paperfraud.config import Config, load_dotenv
from paperfraud.parser.engine import parse_paper
from paperfraud.report.aggregator import aggregate_results
from paperfraud.report.formatter import format_json, format_markdown

load_dotenv()

app = typer.Typer(
    name="paperfraud",
    help="External paper fraud detection CLI — statistical, image, and text forensics",
    no_args_is_help=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def _version_callback(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit",
        is_eager=True,
    ),
):
    """Print version and exit. Only fires when no subcommand is given."""
    if version:
        console.print(f"paperfraud-detect {__version__}")
        raise typer.Exit()


def _build_config(
    grobid_url: str = "",
    llm_provider: str = "noop",
    no_external: bool = False,
    timeout: int = 600,
    skip_images: bool = True,
    max_pages: int = 0,
    data_file: str = "",
    review: bool = False,
    output_dir: str = "",
    launch_web: bool = False,
) -> Config:
    return Config(
        grobid_url=grobid_url,
        llm_provider=llm_provider,
        no_external=no_external,
        timeout=timeout,
        skip_images=skip_images,
        max_pages=max_pages,
        data_file=data_file,
        llm_review=review,
        output_dir=output_dir,
        launch_web=launch_web,
    )


def _run_all_checks(paper, config: Config) -> list[CheckResult]:
    """Run all available checks in parallel and return results."""
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    results: list[CheckResult] = []

    checks = []

    # ── Numbers checks ─────────────────────────────────────────────────────
    try:
        from paperfraud.checks.numbers.py_statcheck import run_py_statcheck
        from paperfraud.checks.numbers.grim import run_grim
        from paperfraud.checks.numbers.digit_pref import run_digit_checks
        from paperfraud.checks.numbers.arithmetic import run_arithmetic_check
        from paperfraud.checks.numbers.benford import run_benford
        from paperfraud.checks.numbers.identical_values import run_identical_values
        checks.extend([
            ("numbers.statcheck", run_py_statcheck),
            ("numbers.grim", run_grim),
            ("numbers.digit", run_digit_checks),
            ("numbers.arithmetic", run_arithmetic_check),
            ("numbers.benford", run_benford),
            ("numbers.identical_values", run_identical_values),
        ])
    except Exception as e:
        console.print(f"[red]✗ numbers 检测模块加载失败: {e}[/red]")

    # ── Image checks ───────────────────────────────────────────────────────
    if paper.image_paths:
        try:
            from paperfraud.checks.images.lut import run_lut
            from paperfraud.checks.images.clone_detect import run_clone_detect
            from paperfraud.checks.images.ela import run_ela
            checks.extend([
                ("images.lut", run_lut),
                ("images.clone_detect", run_clone_detect),
                ("images.ela", run_ela),
            ])
        except Exception as e:
            console.print(f"[red]✗ images 检测模块加载失败: {e}[/red]")

    # ── Text / Stats / Bioinfo checks ──────────────────────────────────────
    if paper.full_text:
        try:
            from paperfraud.checks.text.blacklist import run_blacklist
            from paperfraud.checks.text.pvalue_camouflage import run_pvalue_camouflage
            from paperfraud.checks.text.title_conclusion_gap import run_title_conclusion_gap
            from paperfraud.checks.stats.normality_claim import run_normality_claim
            from paperfraud.checks.stats.fallacies import run_fallacies
            from paperfraud.checks.stats.sample_size import run_sample_size_check
            from paperfraud.checks.stats.p_hacking import run_p_hacking_check
            from paperfraud.checks.stats.method_misuse import run_method_misuse
            from paperfraud.checks.bioinfo.western_blot import run_western_blot_check
            checks.extend([
                ("text.blacklist", run_blacklist),
                ("text.pvalue_camouflage", run_pvalue_camouflage),
                ("text.title_conclusion_gap", run_title_conclusion_gap),
                ("stats.normality_claim", run_normality_claim),
                ("stats.fallacies", run_fallacies),
                ("stats.sample_size", run_sample_size_check),
                ("stats.p_hacking", run_p_hacking_check),
                ("stats.method_misuse", run_method_misuse),
                ("bioinfo.western_blot", run_western_blot_check),
            ])
        except Exception as e:
            console.print(f"[red]✗ text/stats/bioinfo 检测模块加载失败: {e}[/red]")

    # Print head-up for slow image checks
    image_check_ids = {"images.clone_detect", "images.ela", "images.lut"}
    has_image_checks = any(cid in image_check_ids for cid, _ in checks)
    if has_image_checks and paper.image_paths:
        plural = "s" if len(paper.image_paths) != 1 else ""
        console.print(
            f"[dim]图像检测涉及 {len(paper.image_paths)} 张图片{plural}，"
            f"clone_detect 可能耗时 1-3 分钟...[/dim]"
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=False,
    ) as progress:
        task = progress.add_task(f"[cyan]执行 {len(checks)} 项检测...", total=len(checks))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_map = {
                executor.submit(check_fn, paper): check_id
                for check_id, check_fn in checks
            }
            for future in concurrent.futures.as_completed(future_map):
                check_id = future_map[future]
                try:
                    check_results = future.result()
                    if isinstance(check_results, list):
                        results.extend(check_results)
                    else:
                        results.append(check_results)
                except Exception as e:
                    results.append(
                        CheckResult(
                            check_id=check_id,
                            check_name=check_id,
                            level="error",
                            verdict=f"检查执行异常: {e}",
                            needs_human=False,
                        )
                    )
                progress.update(task, advance=1,
                    description=f"[cyan]检测完成: {check_id}")

    return results


@app.command(
    epilog=(
        "示例:\n"
        "  paperfraud check papers/paper.pdf\n"
        "  paperfraud check papers/paper.pdf --extract-images --review -o output/demo\n"
        "  paperfraud check papers/paper.docx --extract-images --no-web\n"
        "  paperfraud check papers/paper.pdf --data-file data.csv --review"
    ),
)
def check(
    target: str = typer.Argument(..., help="DOI or PDF file path"),
    output: str = typer.Option("terminal", "--output", "-o", help="Output format: terminal, json, markdown"),
    output_file: Optional[Path] = typer.Option(None, "--output-file", help="Write output to file"),
    grobid_url: str = typer.Option("", "--grobid-url", help="GROBID service URL"),
    llm_provider: str = typer.Option("noop", "--llm-provider", help="LLM provider: deepseek, anthropic, openai, ollama, noop"),
    no_external: bool = typer.Option(False, "--no-external", help="Disable external cross-validation"),
    timeout: int = typer.Option(600, "--timeout", help="Total timeout in seconds"),
    extract_images: bool = typer.Option(False, "--extract-images", help="Extract embedded images for image forensics (slow for large PDFs)"),
    max_pages: int = typer.Option(0, "--max-pages", help="Max pages to parse (0 = all)"),
    data_file: Optional[Path] = typer.Option(None, "--data-file", help="CSV/TSV file with supplementary numeric data for Benford/GRIM checks"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Persistent output directory for images and report"),
    web: bool = typer.Option(False, "--web/--no-web", help="Launch Streamlit dashboard after check"),
    web_port: int = typer.Option(8502, "--web-port", help="Streamlit server port (used with --web)"),
    review: bool = typer.Option(False, "--review", help="Run LLM qualitative review on detection results (requires DEEPSEEK_API_KEY)"),
):
    """Analyze a paper for potential fraud signals.

    TARGET can be a DOI (10.xxx/xxx) or path to a PDF file.
    """
    target_path = Path(target)

    config = _build_config(
        grobid_url=grobid_url,
        llm_provider=llm_provider,
        no_external=no_external,
        timeout=timeout,
        skip_images=not extract_images,
        max_pages=max_pages,
        data_file=str(data_file) if data_file else "",
        review=review,
        output_dir=str(output_dir) if output_dir else "",
        launch_web=web,
    )

    # Resolve target
    SUPPORTED_SUFFIXES = {".pdf", ".docx"}
    if not target_path.exists():
        console.print(f"[red]文件不存在: {target}[/red]")
        raise typer.Exit(1)
    if target_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        console.print(f"[red]不支持的格式: {target_path.suffix}（支持 {', '.join(sorted(SUPPORTED_SUFFIXES))}）[/red]")
        raise typer.Exit(1)

    file_path = target_path

    console.print(f"[bold]正在解析文件: {file_path.name}[/bold]")

    # Parse
    paper = parse_paper(file_path, config)
    if config.data_file:
        paper.data_file = config.data_file
    else:
        # Auto-detect supplementary data in same directory
        parent = file_path.parent
        stem = file_path.stem
        for ext in (".csv", ".tsv", ".txt"):
            candidate = parent / f"{stem}{ext}"
            if candidate.exists():
                paper.data_file = str(candidate)
                config.data_file = str(candidate)
                console.print(f"[dim]自动关联数据: {candidate.name}[/dim]")
                break
        else:
            csv_files = list(parent.glob("*.csv")) + list(parent.glob("*.tsv"))
            if csv_files:
                paper.data_file = str(csv_files[0])
                config.data_file = str(csv_files[0])
                console.print(f"[dim]自动关联数据: {csv_files[0].name}[/dim]")
    console.print(
        f"[dim]提取文本 {len(paper.full_text)} 字符, "
        f"图片 {len(paper.image_paths)} 张[/dim]"
    )

    # Auto-set output_dir from paper title (for --web or when not specified)
    if not output_dir:
        title = paper.title or file_path.stem
        safe_name = _sanitize_filename(title)
        output_dir = Path(f"output/{safe_name}")
    # Update config + paper with final output_dir
    config.output_dir = str(output_dir)
    paper._output_dir = str(output_dir)

    # Copy original extracted images to output_dir/images/ for web viewer
    if paper.image_paths:
        images_dir = Path(config.output_dir) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        for img_path in paper.image_paths:
            if img_path.exists():
                dest = images_dir / img_path.name
                if not dest.exists():
                    shutil.copy2(img_path, dest)

    # Run checks
    console.print("[bold]正在执行检测...[/bold]")
    results = _run_all_checks(paper, config)

    # Aggregate
    aggregated = aggregate_results(results)

    # LLM qualitative review (before temp cleanup — needs paper data)
    llm_review = None
    if config.llm_review:
        console.print("[bold]正在执行 LLM 定性审查...[/bold]")
        try:
            from paperfraud.review.llm_review import run_llm_review
            llm_review = run_llm_review(paper, aggregated, results, config)
            aggregated["llm_review"] = llm_review.to_dict()
            console.print(f"[dim]LLM 审查完成 (tokens: {llm_review.tokens_used}, provider: {llm_review.provider})[/dim]")
        except Exception as e:
            console.print(f"[yellow]LLM 审查失败: {e}[/yellow]")

    # Clean up temp image directory (only when no persistent output_dir)
    if paper._tmp_dir and not config.output_dir:
        shutil.rmtree(paper._tmp_dir, ignore_errors=True)
        paper._tmp_dir = None

    # Extract figure captions from PDF (for web UI labels)
    figure_captions: dict[str, dict] = {}
    if config.output_dir:
        figure_captions = _extract_figure_captions(file_path)

    # Save JSON report to output_dir if persistent
    if config.output_dir:
        output_path = Path(config.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Collect image artifact paths for the report
        image_artifacts: dict[str, list[str]] = {}
        for subdir_name in ["lut_output", "clone_output", "ela_output"]:
            subdir = output_path / subdir_name
            if subdir.is_dir():
                image_artifacts[subdir_name] = sorted(
                    str(p.relative_to(output_path)) for p in subdir.glob("*.png")
                )
            else:
                image_artifacts[subdir_name] = []

        report_json_path = output_path / "report.json"
        json_out = format_json(aggregated, results, paper, image_artifacts=image_artifacts, figure_captions=figure_captions)
        report_json_path.write_text(json_out, encoding="utf-8")
        console.print(f"[dim]报告已保存到: {report_json_path}[/dim]")

    # Output
    if output == "json":
        out = format_json(aggregated, results, paper)
        if output_file:
            output_file.write_text(out, encoding="utf-8")
            console.print(f"[green]报告已写入: {output_file}[/green]")
        else:
            console.print(out)
    elif output == "markdown":
        title = paper.title or file_path.stem
        out = format_markdown(aggregated, results, title)
        if output_file:
            output_file.write_text(out, encoding="utf-8")
            console.print(f"[green]报告已写入: {output_file}[/green]")
        else:
            console.print(out)
    else:
        _print_terminal_report(aggregated, results, paper)

    # Launch Streamlit if --web
    if config.launch_web and config.output_dir:
        _launch_streamlit(report_path=Path(config.output_dir) / "report.json", port=web_port)


def _extract_figure_captions(pdf_path: Path) -> dict[str, dict]:
    """Extract figure captions from PDF, keyed by page number.

    Returns: {"6": {"figure": "1", "caption": "Figure 1. NDV induces..."}, ...}
    """
    import re

    captions: dict[str, dict] = {}
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        for page_num in range(doc.page_count):
            text = doc[page_num].get_text("text")
            for m in re.finditer(
                r'(?im)^\s*(?:Figure|Fig)\.?\s*(\d+)\b\.?\s*(.+)',
                text,
            ):
                fig_num = m.group(1)
                caption = m.group(2).strip().lstrip("|").strip()
                # Take up to 300 chars, break at next figure or double newline
                caption = re.split(r'\n(?:Figure|Fig)\.?\s*\d+\b', caption)[0]
                caption = caption[:300].strip()
                captions[str(page_num + 1)] = {"figure": fig_num, "caption": caption}
        doc.close()
    except Exception:
        pass
    return captions


def _sanitize_filename(name: str, max_len: int = 80) -> str:
    """Turn a paper title into a safe directory name. Keeps CJK characters."""
    import re
    # Remove chars invalid in filenames across platforms
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    # Replace whitespace and hyphens with single underscore
    name = re.sub(r"[\s\-]+", "_", name)
    # Remove leading/trailing special chars
    name = name.strip("._")
    # Truncate
    if len(name) > max_len:
        name = name[:max_len].rstrip("_")
    return name or "unknown_paper"


def _launch_streamlit(report_path: Path | None = None, reports_dir: Path | None = None, port: int = 8501) -> None:
    """Launch Streamlit dashboard.

    Args:
        report_path: Single report.json to load (--report mode).
        reports_dir: Directory to scan for report.json files (--reports-dir mode).
        port: Streamlit server port.
    """
    import os
    import socket
    import threading
    import webbrowser

    app_path = Path(__file__).resolve().parent / "web" / "app.py"

    if not app_path.exists():
        console.print(f"[red]Streamlit app 不存在: {app_path}[/red]")
        return

    # ── Port detection ────────────────────────────────────────────────────
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_in_use = sock.connect_ex(("localhost", port)) == 0
    sock.close()

    if port_in_use:
        console.print(
            f"[yellow]端口 {port} 已被占用，Web UI 可能已在运行。"
            f"请直接访问 http://localhost:{port}[/yellow]"
        )
        webbrowser.open(f"http://localhost:{port}")
        return

    # ── Build streamlit args ──────────────────────────────────────────────
    streamlit_args = [
        sys.executable, "-m", "streamlit", "run",
        str(app_path),
        "--server.port", str(port),
        "--",
    ]
    if reports_dir is not None:
        streamlit_args.extend(["--reports-dir", str(reports_dir)])
        url = f"http://localhost:{port}"
    elif report_path is not None:
        streamlit_args.extend(["--report", str(report_path)])
        url = f"http://localhost:{port}"
    else:
        url = f"http://localhost:{port}"

    console.print(f"\n[bold green]启动 Streamlit Dashboard...[/bold green]")
    if report_path:
        console.print(f"[dim]报告: {report_path}[/dim]")
    if reports_dir:
        console.print(f"[dim]报告目录: {reports_dir}[/dim]")
    console.print(f"[dim]访问: {url}[/dim]")
    console.print("[dim]按 Ctrl+C 退出[/dim]\n")

    # Open browser after streamlit starts
    def _open_browser():
        import time
        time.sleep(4)
        webbrowser.open(url)
    threading.Thread(target=_open_browser, daemon=True).start()

    subprocess.run(streamlit_args)


def _print_terminal_report(aggregated: dict, results: list[CheckResult], paper):
    """Print a rich terminal report."""
    level_colors = {
        "red": "red",
        "orange": "orange1",
        "yellow": "yellow",
        "green": "green",
        "error": "dim",
    }

    # Header
    title = paper.title or "Unknown Paper"
    console.print()
    console.print(Panel.fit(
        f"[bold]{title}[/bold]",
        title="Paper Fraud Detection Report",
        border_style="blue",
    ))

    # Summary
    overall_color = level_colors.get(aggregated["overall_level"], "white")
    console.print()
    console.print(f"[bold {overall_color}]综合判定: {aggregated['overall_verdict']}[/bold {overall_color}]")
    console.print()

    # Risk Score bar
    risk_score = aggregated.get("risk_score", 0)
    risk_breakdown = aggregated.get("risk_breakdown", {})
    bar_width = 40
    filled = int(risk_score / 100 * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    score_color = "red" if risk_score >= 51 else "yellow" if risk_score >= 31 else "green"
    console.print(f"[bold {score_color}]欺诈风险评分: {risk_score:.0f}/100[/bold {score_color}]")
    console.print(f"[{score_color}]{bar}[/{score_color}]")
    if risk_breakdown.get("veto_triggered"):
        console.print("[red bold]一票否决触发：检测到数学铁证[/red bold]")
    if risk_breakdown.get("floor_applied"):
        console.print("[yellow dim]保底机制已激活：存在红色信号，分数已提升至最低阈值[/yellow dim]")
    if risk_breakdown.get("cluster_bonus", 0) > 0:
        console.print(f"[dim]聚类加分: +{risk_breakdown['cluster_bonus']}（多信号指向同一数据源）[/dim]")
    console.print()

    # Summary table
    table = Table(title="检测汇总")
    table.add_column("级别", style="bold")
    table.add_column("数量", justify="right")
    table.add_row("🔴 Red", str(aggregated["red_count"]), style="red")
    table.add_row("🟠 Orange", str(aggregated["orange_count"]), style="orange1")
    table.add_row("🟡 Yellow", str(aggregated["yellow_count"]), style="yellow")
    table.add_row("🟢 Green", str(aggregated["green_count"]), style="green")
    table.add_row("⚪ Error", str(aggregated["error_count"]), style="dim")
    table.add_row("总计", str(aggregated["total_checks"]), style="bold")
    console.print(table)

    if aggregated["needs_human"]:
        console.print(f"\n[yellow]⚠️  {aggregated['needs_human_count']} 项需要人工复核[/yellow]")

    # Detail by level
    for level in ["red", "orange", "yellow"]:
        level_results = [r for r in results if r.level == level]
        if not level_results:
            continue

        console.print()
        console.print(f"[bold {level_colors[level]}]── {level.upper()} 信号 ──[/bold {level_colors[level]}]")

        for r in level_results:
            console.print(f"  [{level_colors[level]}]● {r.check_name}[/{level_colors[level]}]")
            console.print(f"    {r.verdict}")
            if r.evidence:
                for e in r.evidence[:3]:
                    console.print(f"    [dim]{e}[/dim]")
            if r.needs_human:
                console.print(f"    [yellow]→ {r.human_instruction}[/yellow]")

    # Green summary
    green_count = sum(1 for r in results if r.level == "green")
    error_count = sum(1 for r in results if r.level == "error")
    console.print()
    console.print(f"[dim]🟢 {green_count} 项通过, ⚪ {error_count} 项无法执行[/dim]")

    # LLM Review section
    llm_review = aggregated.get("llm_review")
    if llm_review:
        console.print()
        console.print(Panel.fit(
            "[bold cyan]LLM 定性审查[/bold cyan]",
            border_style="cyan",
        ))
        console.print()
        console.print(f"[bold]综合判断：[/bold]{llm_review.get('overall_assessment', '')}")
        console.print()
        severity = llm_review.get("severity_score", 0)
        sev_color = "red" if severity >= 7 else "yellow" if severity >= 4 else "green"
        console.print(f"[bold {sev_color}]造假嫌疑评分：{severity}/10[/bold {sev_color}]")
        console.print()

        # Signal reviews table
        signal_reviews = llm_review.get("signal_reviews", [])
        if signal_reviews:
            sr_table = Table(title="逐信号审查")
            sr_table.add_column("检测项", style="bold")
            sr_table.add_column("判定", justify="center")
            sr_table.add_column("严重程度", justify="center")
            sr_table.add_column("理由")

            for sr in signal_reviews:
                verdict = "[green]真锤[/green]" if sr.get("is_true_positive") else "[dim]假阳性[/dim]"
                sev_label = {
                    "high": "[red]高[/red]",
                    "medium": "[yellow]中[/yellow]",
                    "low": "[dim]低[/dim]",
                    "false_alarm": "[dim]误报[/dim]",
                }.get(sr.get("severity", ""), sr.get("severity", ""))
                reason = sr.get("reasoning", "")[:80]
                sr_table.add_row(sr.get("check_id", ""), verdict, sev_label, reason)

            console.print(sr_table)

        # PubPeer draft
        pubpeer = llm_review.get("pubpeer_draft", "")
        if pubpeer:
            console.print()
            console.print(Panel(
                pubpeer,
                title="[bold]PubPeer 审稿草稿[/bold]",
                border_style="cyan",
            ))

    console.print()


@app.command()
def review(
    json_file: Path = typer.Argument(..., help="Path to JSON report from paperfraud check --output json"),
    llm_provider: str = typer.Option("deepseek", "--llm-provider", help="LLM provider: deepseek, anthropic, openai"),
):
    """Re-run LLM qualitative review on a saved JSON report."""
    import json as _json

    if not json_file.exists():
        console.print(f"[red]文件不存在: {json_file}[/red]")
        raise typer.Exit(1)

    data = _json.loads(json_file.read_text(encoding="utf-8"))

    config = Config(
        llm_provider=llm_provider,
        llm_review=True,
    )

    # Reconstruct a minimal ParsedPaper from the report
    from paperfraud.base import ParsedPaper

    summary = data.get("summary", {})
    checks_data = data.get("checks", [])

    paper = ParsedPaper(
        title=summary.get("title", ""),
        journal=summary.get("journal", ""),
        year=summary.get("year"),
    )

    results = []
    for c in checks_data:
        results.append(CheckResult(
            check_id=c.get("check_id", ""),
            check_name=c.get("check_name", ""),
            level=c.get("level", "error"),
            verdict=c.get("verdict", ""),
            evidence=c.get("evidence", []),
            confidence=c.get("confidence", 1.0),
            needs_human=c.get("needs_human", False),
            human_instruction=c.get("human_instruction", ""),
        ))

    aggregated = {
        "red_count": summary.get("red_count", 0),
        "orange_count": summary.get("orange_count", 0),
        "yellow_count": summary.get("yellow_count", 0),
        "green_count": summary.get("green_count", 0),
        "error_count": summary.get("error_count", 0),
        "total_checks": summary.get("total_checks", len(results)),
        "overall_level": summary.get("overall_level", "error"),
        "overall_verdict": summary.get("overall_verdict", ""),
        "needs_human_count": summary.get("needs_human_count", 0),
    }

    console.print("[bold]正在执行 LLM 定性审查...[/bold]")
    try:
        from paperfraud.review.llm_review import run_llm_review
        llm_result = run_llm_review(paper, aggregated, results, config)
    except Exception as e:
        console.print(f"[red]LLM 审查失败: {e}[/red]")
        raise typer.Exit(1)

    # Write LLM review back to the original report.json
    data["llm_review"] = llm_result.to_dict()
    json_file.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[dim]LLM 审查已写回: {json_file}[/dim]")

    # Print review
    console.print()
    console.print(Panel.fit("[bold cyan]LLM 定性审查[/bold cyan]", border_style="cyan"))
    console.print()
    console.print(f"[bold]综合判断：[/bold]{llm_result.overall_assessment}")
    console.print()
    severity = llm_result.severity_score
    sev_color = "red" if severity >= 7 else "yellow" if severity >= 4 else "green"
    console.print(f"[bold {sev_color}]造假嫌疑评分：{severity}/10[/bold {sev_color}]")
    console.print()

    if llm_result.signal_reviews:
        sr_table = Table(title="逐信号审查")
        sr_table.add_column("检测项", style="bold")
        sr_table.add_column("判定", justify="center")
        sr_table.add_column("严重程度", justify="center")
        sr_table.add_column("理由")
        for sr in llm_result.signal_reviews:
            verdict = "[green]真锤[/green]" if sr.is_true_positive else "[dim]假阳性[/dim]"
            sev_label = {
                "high": "[red]高[/red]",
                "medium": "[yellow]中[/yellow]",
                "low": "[dim]低[/dim]",
                "false_alarm": "[dim]误报[/dim]",
            }.get(sr.severity, sr.severity)
            sr_table.add_row(sr.check_id, verdict, sev_label, sr.reasoning[:80])
        console.print(sr_table)

    if llm_result.pubpeer_draft:
        console.print()
        console.print(Panel(
            llm_result.pubpeer_draft,
            title="[bold]PubPeer 审稿草稿[/bold]",
            border_style="cyan",
        ))

    console.print(f"\n[dim](tokens: {llm_result.tokens_used}, provider: {llm_result.provider})[/dim]")


@app.command()
def serve(
    directory: Path = typer.Argument(..., help="Directory containing report.json files (e.g. output/)"),
    port: int = typer.Option(8502, "--port", "-p", help="Streamlit server port"),
):
    """Launch Streamlit dashboard with multi-report switching.

    Scans DIRECTORY recursively for report.json files and shows all
    detected papers in a sidebar dropdown for quick switching.
    """
    if not directory.is_dir():
        console.print(f"[red]目录不存在: {directory}[/red]")
        raise typer.Exit(1)

    # Scan for report.json files (recursive, max 3 levels)
    reports = list(directory.rglob("report.json"))
    # Filter: only keep reports within 3 levels of directory
    reports = [r for r in reports if len(r.relative_to(directory).parts) <= 3]

    if not reports:
        console.print(f"[yellow]目录下未找到 report.json 文件: {directory}[/yellow]")
        console.print("[dim]提示：先运行 paperfraud check paper.pdf --output-dir output/<name>/[/dim]")
        raise typer.Exit(1)

    console.print(f"[dim]扫描到 {len(reports)} 个报告[/dim]")
    for r in reports:
        console.print(f"  [dim]- {r}[/dim]")

    _launch_streamlit(reports_dir=directory, port=port)


@app.command()
def extract_images(
    target: str = typer.Argument(..., help="PDF or DOCX file path"),
    output_dir: Path = typer.Option(..., "--output-dir", "-o", help="Output directory for extracted images"),
    max_pages: int = typer.Option(0, "--max-pages", help="Max pages to parse (0=all)"),
):
    """Extract embedded images and figure regions from a PDF or DOCX without running fraud checks."""
    target_path = Path(target)
    SUPPORTED_SUFFIXES = {".pdf", ".docx"}
    if not target_path.exists() or target_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        console.print(f"[red]无效的文件: {target}（支持 .pdf 和 .docx）[/red]")
        raise typer.Exit(1)

    config = Config(
        skip_images=False,
        max_pages=max_pages,
    )

    console.print(f"[bold]正在提取图片: {target_path.name}[/bold]")
    paper = parse_paper(target_path, config)

    if not paper.image_paths:
        console.print("[yellow]未提取到任何图片。[/yellow]")
        raise typer.Exit(0)

    output_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for img_path in paper.image_paths:
        if img_path.exists():
            dest = output_dir / img_path.name
            shutil.copy2(img_path, dest)
            copied += 1

    page_count = len({p.stem.rsplit("_", 1)[0] for p in paper.image_paths})
    console.print(f"[green]提取完成：{copied} 张图片（{page_count} 页） → {output_dir}[/green]")

    if paper._tmp_dir:
        shutil.rmtree(paper._tmp_dir, ignore_errors=True)


@app.command()
def doctor():
    """Check environment and dependencies — diagnose common setup issues."""
    console.print()
    console.print(Panel.fit(
        "[bold]PaperFraud Detect — 环境检查[/bold]",
        border_style="blue",
    ))
    console.print()

    all_ok = True

    # ── Python version ─────────────────────────────────────────────────────
    py_version = sys.version_info
    if py_version >= (3, 9):
        console.print(f"  ✅ Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    else:
        console.print(f"  ❌ Python {py_version.major}.{py_version.minor}.{py_version.micro}（需要 >= 3.9）")
        console.print(f"     [修复] 请安装 Python 3.9 或更高版本: https://www.python.org/downloads/")
        all_ok = False

    # ── Core dependencies ──────────────────────────────────────────────────
    core_deps = [
        ("PyMuPDF", "fitz", "pip install PyMuPDF"),
        ("numpy", "numpy", "pip install numpy"),
        ("scipy", "scipy", "pip install scipy"),
        ("Pillow", "PIL", "pip install Pillow"),
        ("opencv-python-headless", "cv2", "pip install opencv-python-headless"),
        ("typer", "typer", "pip install typer"),
        ("rich", "rich", "pip install rich"),
        ("Jinja2", "jinja2", "pip install Jinja2"),
        ("httpx", "httpx", "pip install httpx"),
        ("PyYAML", "yaml", "pip install PyYAML"),
    ]
    for name, import_name, fix_cmd in core_deps:
        try:
            __import__(import_name)
            console.print(f"  ✅ {name}")
        except ImportError:
            console.print(f"  ❌ {name} 未安装")
            console.print(f"     [修复] 请运行: {fix_cmd}")
            all_ok = False

    # ── Optional dependencies ──────────────────────────────────────────────
    console.print()
    console.print("[dim]可选依赖（增强功能）：[/dim]")
    optional_deps = [
        ("streamlit (Web UI)", "streamlit", "pip install streamlit"),
        ("python-docx (Word 支持)", "docx", "pip install python-docx"),
    ]
    for name, import_name, fix_cmd in optional_deps:
        try:
            __import__(import_name)
            console.print(f"  ✅ {name}")
        except ImportError:
            console.print(f"  ⚪ {name} — 未安装（可选）")
            console.print(f"     [提示] 需要时请运行: {fix_cmd}")

    # ── API Keys ───────────────────────────────────────────────────────────
    console.print()
    console.print("[dim]LLM API Key（至少配一个才能用 --review）：[/dim]")
    api_keys = [
        ("DEEPSEEK_API_KEY", "DeepSeek（推荐，便宜快速）"),
        ("ANTHROPIC_API_KEY", "Anthropic Claude"),
        ("OPENAI_API_KEY", "OpenAI GPT-4o"),
    ]
    any_key_set = False
    for key_name, description in api_keys:
        val = os.environ.get(key_name, "")
        if val:
            masked = val[:4] + "..." + val[-4:] if len(val) > 8 else "***"
            console.print(f"  ✅ {key_name} = {masked}")
            any_key_set = True
        else:
            console.print(f"  ⚪ {key_name} — 未设置")
    if not any_key_set:
        console.print("     [提示] 至少配置一个 API Key 以启用 LLM 审查功能")
        console.print("     [修复] 请运行: export DEEPSEEK_API_KEY=sk-...")

    # ── Papers directory ───────────────────────────────────────────────────
    console.print()
    papers_dir = Path(__file__).resolve().parent.parent / "papers"
    if papers_dir.is_dir():
        pdf_count = len(list(papers_dir.glob("**/*.pdf"))) + len(list(papers_dir.glob("**/*.docx")))
        if pdf_count > 0:
            console.print(f"  ✅ papers/ 目录: {pdf_count} 篇待检测论文")
        else:
            console.print(f"  ⚪ papers/ 目录存在但无 PDF/DOCX 文件")
            console.print(f"     [提示] 将待检测论文放入: {papers_dir}")
    else:
        console.print(f"  ⚪ papers/ 目录不存在")
        console.print(f"     [提示] 创建目录并将论文放入: {papers_dir}")

    # ── Summary ────────────────────────────────────────────────────────────
    console.print()
    if all_ok:
        console.print("[bold green]✅ 环境检查通过，可以开始使用！[/bold green]")
        console.print()
        console.print("[dim]快速开始:[/dim]")
        if any_key_set:
            console.print(f"[dim]  paperfraud check {papers_dir}/your-paper.pdf --extract-images --review -o output/demo[/dim]")
        else:
            console.print(f"[dim]  paperfraud check {papers_dir}/your-paper.pdf --extract-images -o output/demo[/dim]")
    else:
        console.print("[bold yellow]⚠️ 存在未解决的问题，请按上方 [修复] 提示操作[/bold yellow]")

    console.print()


# ── Crawler subcommand ───────────────────────────────────────────────────
try:
    from paperfraud.crawler.cli import crawl_app
    app.add_typer(crawl_app, name="crawl")
except ImportError:
    pass


def main():
    app()


if __name__ == "__main__":
    main()
