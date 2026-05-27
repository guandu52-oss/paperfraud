"""For Better Science RSS parser.

Parses the WordPress RSS 2.0 feed at forbetterscience.com/feed/.
Full-text extraction via httpx GET + html.parser.HTMLParser.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from xml.etree import ElementTree

import httpx

from paperfraud.crawler.base import CrawledPost

FBS_RSS_URL = "https://forbetterscience.com/feed/"

DOI_RE = re.compile(r"10\.\d{4,}/[^\s\"'<>]+")
PMID_RE = re.compile(r"\b\d{8}\b")


class _TextExtractor(HTMLParser):
    """Extract plain text from HTML, skipping script/style tags."""

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def _extract_dois(text: str) -> list[str]:
    return list(set(DOI_RE.findall(text)))


def _extract_pmids(text: str) -> list[str]:
    return list(set(PMID_RE.findall(text)))


def _extract_full_text(html: str) -> str:
    """Extract main content from an FBS article HTML page."""
    # Try to locate the article body
    article_match = re.search(
        r"<article[^>]*>(.*?)</article>",
        html, re.DOTALL | re.IGNORECASE,
    )
    if article_match:
        html = article_match.group(1)

    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser.text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def fetch_feed(client: httpx.AsyncClient | None = None) -> list[CrawledPost]:
    """Fetch and parse the FBS RSS feed. Returns CrawledPost list."""
    close_client = client is None
    if close_client:
        client = httpx.AsyncClient(timeout=30)

    posts: list[CrawledPost] = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        resp = await client.get(
            FBS_RSS_URL,
            headers={"User-Agent": "paperfraud-detect/0.2.0"},
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
    except Exception:
        if close_client:
            await client.aclose()
        return posts

    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    for item in root.iter("item"):
        guid = item.findtext("guid", "")
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        creator = item.findtext("{http://purl.org/dc/elements/1.1/}creator", "")
        description = item.findtext("description", "")

        # Use content:encoded if available, otherwise description
        content_encoded = item.findtext("content:encoded", "", ns)
        content = content_encoded or description

        # Clean HTML from description if it's the only content
        if not content_encoded and description:
            parser = _TextExtractor()
            parser.feed(description)
            content = " ".join(parser.text)

        posts.append(CrawledPost(
            source="forbetterscience",
            source_id=f"fbs:{guid}",
            title=title,
            url=link,
            author=creator,
            date=pub_date,
            content=content,
            dois=_extract_dois(content + title),
            pmids=_extract_pmids(content + title),
            fetched_at=now,
        ))

    if close_client:
        await client.aclose()
    return posts


async def fetch_full_text(
    url: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch and extract full text from an FBS article URL."""
    close_client = client is None
    if close_client:
        client = httpx.AsyncClient(timeout=30)

    try:
        resp = await client.get(
            url,
            headers={"User-Agent": "paperfraud-detect/0.2.0"},
        )
        resp.raise_for_status()
        return _extract_full_text(resp.text)
    except Exception:
        return ""
    finally:
        if close_client:
            await client.aclose()
