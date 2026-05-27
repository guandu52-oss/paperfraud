"""PubPeer API client.

Uses PubPeer's internal search API (Laravel CSRF-protected).
The public pubsearch endpoint was retired in 2024; this uses the
same endpoint the SPA frontend calls.

Limitation: comment text requires authentication (POST /api/publication/{id}/comments
returns 401 without login). Publication metadata (title, abstract, authors) is available.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from paperfraud.crawler.base import CrawledPost

PUBPEER_HOME = "https://pubpeer.com"
SEARCH_URL = "https://pubpeer.com/api/search/"
DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"'<>]+")
PMID_RE = re.compile(r"\b\d{8}\b")


def _extract_dois(text: str) -> list[str]:
    return list(set(DOI_RE.findall(text)))


def _extract_pmids(text: str) -> list[str]:
    return list(set(PMID_RE.findall(text)))


async def _get_csrf_token(client: httpx.AsyncClient) -> str:
    """Fetch the CSRF token from PubPeer's home page."""
    try:
        resp = await client.get(
            PUBPEER_HOME,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        resp.raise_for_status()
        match = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


async def fetch_recent_publications(
    keyword: str = "image manipulation",
    max_results: int = 50,
    client: httpx.AsyncClient | None = None,
) -> list[CrawledPost]:
    """Search PubPeer and return publications with metadata.

    Searches for the given keyword, then fetches publication details
    for each result. Comment text is NOT available without login.
    """
    close_client = client is None
    if close_client:
        client = httpx.AsyncClient(timeout=30, follow_redirects=True)

    posts: list[CrawledPost] = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        csrf = await _get_csrf_token(client)
        if not csrf:
            if close_client:
                await client.aclose()
            return posts

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }

        # Step 1: Search
        search_resp = await client.get(
            f"{SEARCH_URL}?q={keyword}&token={csrf}",
            headers=headers,
        )
        search_resp.raise_for_status()
        data = search_resp.json()
        publications = data.get("publications", [])

        # Step 2: Fetch detail for each publication
        for pub in publications[:max_results]:
            pubpeer_id = pub.get("pubpeer_id", "")
            title = pub.get("title", "") or ""
            abstract = pub.get("abstract", "") or ""

            # Build a basic post from search results
            content_parts = []
            if abstract:
                content_parts.append(abstract)
            if pub.get("comments_total", 0) > 0:
                content_parts.append(
                    f"[PubPeer 上共有 {pub['comments_total']} 条评论]"
                )

            posts.append(CrawledPost(
                source="pubpeer",
                source_id=f"pubpeer:{pubpeer_id}",
                title=title,
                url=f"https://pubpeer.com/publications/{pubpeer_id}",
                author="",
                date=pub.get("published_at", ""),
                content="\n".join(content_parts),
                dois=_extract_dois(title + abstract),
                pmids=_extract_pmids(title + abstract),
                fetched_at=now,
            ))

    except Exception:
        pass
    finally:
        if close_client:
            await client.aclose()

    return posts
