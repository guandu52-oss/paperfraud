"""PubPeer integration — search for existing comments on a paper.

Uses PubPeer's public search endpoint. Rate-limited, no authentication required.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Any


PUBPEER_SEARCH_URL = "https://pubpeer.com/api/pubsearch"


def search_pubpeer(title: str = "", doi: str = "") -> dict[str, Any]:
    """Search PubPeer for comments on a paper.

    Returns dict with:
      - has_comments: bool
      - comments: list of {title, content, date, author} (may be partial)
      - error: str or None
      - url: str — PubPeer URL if found
    """
    result: dict[str, Any] = {
        "has_comments": False,
        "comments": [],
        "error": None,
        "url": "",
    }

    query = doi if doi else title[:300]
    if not query.strip():
        result["error"] = "未提供标题或 DOI"
        return result

    params = urllib.parse.urlencode({"search": query})
    url = f"{PUBPEER_SEARCH_URL}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paperfraud-detect/0.2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        result["error"] = f"PubPeer HTTP {e.code}"
        return result
    except Exception as e:
        result["error"] = f"PubPeer 查询失败: {e}"
        return result

    publications = data.get("publications", []) if isinstance(data, dict) else data if isinstance(data, list) else []

    if not publications:
        return result

    pub = publications[0]
    result["url"] = pub.get("url", "")

    reviews = pub.get("reviews", [])
    if not reviews:
        return result

    result["has_comments"] = True
    for review in reviews[:10]:
        comment = review.get("comment", {})
        result["comments"].append({
            "user": review.get("user_name", review.get("user", {}).get("name", "Anonymous")),
            "date": review.get("date", ""),
            "content": (comment.get("text", "") or "")[:2000],
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

    lines.append("注意：如果 PubPeer 评论提出了已在社区引发讨论的质疑，")
    lines.append("请在你的审查意见中考虑这些质疑角度，但不要简单重复已有评论。")
    lines.append("")

    return "\n".join(lines)
