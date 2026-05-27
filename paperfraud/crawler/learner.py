"""LLM pattern extraction from crawled posts.

Uses DeepSeek (via openai SDK) to extract fraud techniques from individual
posts. Runs one-at-a-time with async concurrency, not batch-merged.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from paperfraud.crawler.base import CrawledPost, LearnedPattern
from paperfraud.crawler.store import (
    get_unlearned_posts,
    insert_pattern,
    init_db,
)

LEARNER_SYSTEM_PROMPT = """你是一位学术论文造假模式分析专家。你会收到一篇关于论文造假的文章或评论。

请从文章中提取论文造假手法，并输出 JSON：

{
  "patterns": [
    {
      "category": "blacklist 或 camouflage",
      "technique": "造假手法的简短描述（中文，1-2句）",
      "detection_hint": "如何在论文中检测这种手法的建议（中文，1-2句）",
      "severity": "high / medium / low",
      "pattern": "具体的关键词/短语/句式（英文）"
    }
  ]
}

分类规则：
- blacklist：单词/短语级别的特征（如特定词汇、固定表述），可以用正则/关键词匹配检测
- camouflage：句式/模板级别的特征（如论文工厂的标准句式），需要模糊匹配/结构匹配

如果没有发现新的造假手法，返回 {"patterns": []}。
最多提取 3 个手法。只提取具体的、可操作的模式，不要泛泛而谈。"""


def _truncate_content(content: str, max_chars: int = 12000) -> str:
    """Truncate long content: keep first ~8000 + last ~4000 chars."""
    if len(content) <= max_chars:
        return content
    head = int(max_chars * 0.7)
    tail = int(max_chars * 0.3)
    return content[:head] + "\n\n[... 中间内容已截断 ...]\n\n" + content[-tail:]


def _parse_llm_response(raw: str) -> list[dict]:
    """Parse the JSON response from the learner LLM."""
    raw = raw.strip()
    try:
        data = json.loads(raw)
        return data.get("patterns", [])
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            return data.get("patterns", [])
        except json.JSONDecodeError:
            pass

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return data.get("patterns", [])
        except json.JSONDecodeError:
            pass

    return []


async def _analyze_post(
    post: dict,
    client,
    model: str,
    sem: asyncio.Semaphore,
) -> list[LearnedPattern]:
    """Analyze a single post via LLM. Returns extracted patterns."""
    async with sem:
        content = _truncate_content(post["content"])
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": LEARNER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"文章标题：{post['title']}\n\n文章内容：\n{content}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2048,
            )
            raw = resp.choices[0].message.content or ""
        except Exception:
            return []

    patterns_raw = _parse_llm_response(raw)
    results: list[LearnedPattern] = []
    for p in patterns_raw:
        category = p.get("category", "blacklist")
        if category not in ("blacklist", "camouflage"):
            category = "blacklist"
        results.append(LearnedPattern(
            post_id=post["source_id"],
            category=category,
            technique=p.get("technique", ""),
            detection_hint=p.get("detection_hint", ""),
            severity=p.get("severity", "medium"),
            reviewed=0,
        ))
    return results


async def run_learner(
    db_path: Path,
    concurrency: int = 3,
    model: str = "deepseek-chat",
) -> int:
    """Analyze unlearned posts and extract fraud patterns.

    Returns the number of new patterns extracted.
    """
    from openai import AsyncOpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY 环境变量")

    init_db(db_path)
    posts = get_unlearned_posts(db_path, limit=20)

    if not posts:
        return 0

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    sem = asyncio.Semaphore(concurrency)

    tasks = [_analyze_post(p, client, model, sem) for p in posts]
    results = await asyncio.gather(*tasks)

    total = 0
    for patterns in results:
        for pattern in patterns:
            if pattern.technique and pattern.detection_hint:
                insert_pattern(db_path, pattern)
                total += 1

    return total
