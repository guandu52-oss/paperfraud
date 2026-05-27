"""PubPeer integration — search for existing comments on a paper.

Note: PubPeer's legacy public API (pubsearch) was shut down in 2024.
The replacement internal API requires CSRF tokens for search and
authentication for comment text. search_pubpeer() now returns
metadata-only results (comment count, URL) without full text.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

PUBPEER_HOME = "https://pubpeer.com"
SEARCH_URL = "https://pubpeer.com/api/search/"


async def _get_csrf_token(client: httpx.AsyncClient) -> str:
    try:
        resp = await client.get(
            PUBPEER_HOME,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        match = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', resp.text)
        return match.group(1) if match else ""
    except Exception:
        return ""


async def _search_async(query: str) -> dict[str, Any]:
    """Async search for a paper on PubPeer."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        csrf = await _get_csrf_token(client)
        if not csrf:
            return {"error": "无法获取 PubPeer CSRF token"}

        try:
            resp = await client.get(
                f"{SEARCH_URL}?q={query}&token={csrf}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": f"PubPeer 查询失败: {e}"}


def search_pubpeer(title: str = "", doi: str = "") -> dict[str, Any]:
    """Search PubPeer for comments on a paper.

    Returns metadata (comment count, URL) but NOT comment text,
    which requires PubPeer login.
    """
    result: dict[str, Any] = {
        "has_comments": False,
        "comments": [],
        "error": None,
        "url": "",
    }

    query = doi.strip() if doi else title[:300].strip()
    if not query:
        result["error"] = "未提供标题或 DOI"
        return result

    try:
        import asyncio
        data = asyncio.run(_search_async(query))
    except Exception as e:
        result["error"] = f"PubPeer 查询失败: {e}"
        return result

    if "error" in data:
        result["error"] = data["error"]
        return result

    publications = data.get("publications", [])
    if not publications:
        return result

    pub = publications[0]
    pubpeer_id = pub.get("pubpeer_id", "")
    result["url"] = f"https://pubpeer.com/publications/{pubpeer_id}" if pubpeer_id else ""

    comments_total = pub.get("comments_total", 0)
    if comments_total > 0:
        result["has_comments"] = True
        result["comments"].append({
            "user": "PubPeer",
            "date": pub.get("last_commented", ""),
            "content": f"该论文在 PubPeer 上有 {comments_total} 条评论。请访问 {result['url']} 查看详情。",
        })

    return result


def format_pubpeer_context(pubpeer_result: dict[str, Any]) -> str:
    """Format PubPeer results as a string for inclusion in LLM review prompt."""
    if not pubpeer_result.get("has_comments"):
        if pubpeer_result.get("url"):
            return f"PubPeer 页面（无评论）: {pubpeer_result['url']}"
        return ""

    lines = ["## PubPeer 已有评论", ""]
    if pubpeer_result.get("url"):
        lines.append(f"链接: {pubpeer_result['url']}")
        lines.append("")

    for i, c in enumerate(pubpeer_result.get("comments", []), 1):
        date_str = f" ({c['date']})" if c.get("date") else ""
        lines.append(f"### 评论 {i} — {c['user']}{date_str}")
        lines.append(c["content"][:2000])
        lines.append("")

    lines.append("注意：评论全文需登录 PubPeer 查看。")
    lines.append("")

    return "\n".join(lines)
