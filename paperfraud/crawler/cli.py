"""CLI subcommands for the crawler module.

Usage:
    paperfraud crawl --sync
    paperfraud crawl --learn
    paperfraud crawl --search "query"
    paperfraud crawl --stats
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from paperfraud.crawler.store import (
    get_stats,
    init_db,
    search_posts,
    upsert_post,
)
from paperfraud.crawler.pubpeer_api import fetch_recent_publications
from paperfraud.crawler.fbs_rss import fetch_feed, fetch_full_text

crawl_app = typer.Typer(
    name="crawl",
    help="论文造假信息爬虫 — PubPeer + For Better Science",
    no_args_is_help=True,
)
console = Console()


def _get_db_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "paperfraud_data" / "crawler.db"


@crawl_app.command()
def sync(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Fetch but don't write to database"
    ),
    keyword: str = typer.Option(
        "fraud", "--keyword", "-k", help="PubPeer search keyword"
    ),
):
    """Incremental sync from PubPeer API + FBS RSS."""

    db_path = _get_db_path()
    init_db(db_path)

    total_new = 0

    # ── PubPeer ──────────────────────────────────────────────────────────
    console.print("[bold]正在从 PubPeer 拉取数据...[/bold]")
    try:
        pubpeer_posts = asyncio.run(fetch_recent_publications(keyword=keyword))
        console.print(f"  PubPeer 返回 {len(pubpeer_posts)} 条结果")
        new_pp = 0
        for post in pubpeer_posts:
            if not dry_run:
                if upsert_post(db_path, post):
                    new_pp += 1
            else:
                new_pp += 1
        total_new += new_pp
        console.print(f"  [green]PubPeer 新增: {new_pp}[/green]")
    except Exception as e:
        console.print(f"  [red]PubPeer 失败: {e}[/red]")

    # ── FBS RSS ──────────────────────────────────────────────────────────
    console.print("[bold]正在从 For Better Science RSS 拉取数据...[/bold]")
    try:
        fbs_posts = asyncio.run(fetch_feed())
        console.print(f"  FBS RSS 返回 {len(fbs_posts)} 条")

        # Fetch full text for each article
        import httpx

        new_fbs = 0
        async def _enrich_and_save():
            nonlocal new_fbs
            async with httpx.AsyncClient(timeout=30) as client:
                for post in fbs_posts:
                    if post.url:
                        full = await fetch_full_text(post.url, client)
                        if full:
                            post.content = full
                    if not dry_run:
                        if upsert_post(db_path, post):
                            new_fbs += 1
                    else:
                        new_fbs += 1

        asyncio.run(_enrich_and_save())
        total_new += new_fbs
        console.print(f"  [green]FBS 新增: {new_fbs}[/green]")
    except Exception as e:
        console.print(f"  [red]FBS 失败: {e}[/red]")

    console.print(f"[bold green]同步完成，新增 {total_new} 条帖子[/bold green]")


@crawl_app.command()
def learn(
    concurrency: int = typer.Option(
        3, "--concurrency", "-c", help="LLM 并发数"
    ),
):
    """Run LLM analysis on new posts to extract fraud patterns."""

    db_path = _get_db_path()
    init_db(db_path)

    console.print("[bold]正在用 LLM 分析未处理的帖子...[/bold]")
    try:
        from paperfraud.crawler.learner import run_learner

        num = asyncio.run(run_learner(db_path, concurrency=concurrency))
        console.print(f"[green]新提取 {num} 条候选规则[/green]")
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]LLM 分析失败: {e}[/red]")
        raise typer.Exit(1)


@crawl_app.command()
def search(
    query: str = typer.Argument(..., help="Search query (FTS5)"),
):
    """Full-text search of crawled posts."""

    db_path = _get_db_path()
    init_db(db_path)

    results = search_posts(db_path, query)
    if not results:
        console.print("[yellow]未找到匹配结果[/yellow]")
        return

    for post in results:
        console.print(f"[bold]{post['title'][:120]}[/bold]")
        console.print(f"  {post['source']} | {post['date']} | {post['url']}")
        console.print(f"  {post['content'][:200]}...")
        console.print()


@crawl_app.command()
def stats():
    """Show crawler database statistics."""

    db_path = _get_db_path()
    init_db(db_path)

    s = get_stats(db_path)
    console.print(f"PubPeer: {s['pubpeer']} posts")
    console.print(f"For Better Science: {s['forbetterscience']} posts")
    console.print(f"候选规则: {s['pending']} 条待审核")
    console.print(f"总计: {s['total']} posts")
