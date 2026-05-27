"""Core dataclasses for the crawler module."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CrawledPost:
    """A post crawled from PubPeer or For Better Science."""

    source: str          # "pubpeer" | "forbetterscience"
    source_id: str       # 站内唯一 ID（去重用）
    title: str
    url: str
    author: str
    date: str            # ISO 8601
    content: str         # 全文或摘要
    dois: list[str] = field(default_factory=list)
    pmids: list[str] = field(default_factory=list)
    fetched_at: str = ""


@dataclass
class LearnedPattern:
    """LLM-extracted fraud pattern, pending human review."""

    post_id: str         # FK → CrawledPost.source_id
    category: str        # "blacklist" | "camouflage"
    technique: str       # 造假手法描述
    detection_hint: str  # 检测建议
    severity: str        # high / medium / low
    reviewed: int = 0    # 0=未审, 1=采纳, -1=拒绝
