"""LLM review engine — calls DeepSeek/Anthropic/OpenAI for qualitative review."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from paperfraud.base import CheckResult, ParsedPaper
from paperfraud.config import Config
from paperfraud.review.prompts import SYSTEM_PROMPT, build_review_prompt


@dataclass
class SignalReview:
    """LLM's judgment on a single signal."""
    check_id: str
    is_true_positive: bool
    reasoning: str
    severity: str  # high, medium, low, false_alarm


@dataclass
class LLMReviewResult:
    """Structured output from LLM qualitative review."""
    overall_assessment: str
    severity_score: int
    signal_reviews: list[SignalReview] = field(default_factory=list)
    pubpeer_draft: str = ""
    tokens_used: int = 0
    provider: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "overall_assessment": self.overall_assessment,
            "severity_score": self.severity_score,
            "signal_reviews": [
                {
                    "check_id": sr.check_id,
                    "is_true_positive": sr.is_true_positive,
                    "reasoning": sr.reasoning,
                    "severity": sr.severity,
                }
                for sr in self.signal_reviews
            ],
            "pubpeer_draft": self.pubpeer_draft,
            "tokens_used": self.tokens_used,
            "provider": self.provider,
            "model": self.model,
        }


def run_llm_review(
    paper: ParsedPaper,
    aggregated: dict,
    results: list[CheckResult],
    config: Config,
) -> LLMReviewResult:
    """Run LLM qualitative review on the detection results.

    Dispatches to the appropriate provider based on config.llm_provider.
    """
    user_prompt = build_review_prompt(paper, aggregated, results)

    provider = config.llm_provider or "deepseek"

    if provider == "anthropic":
        return _call_anthropic(user_prompt, config)
    elif provider == "openai":
        return _call_openai(user_prompt, config)
    else:
        return _call_deepseek(user_prompt, config)


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from LLM response, with defensive fallback.

    Tries:
      1. Direct JSON parse
      2. Extract from ```json ... ``` block
      3. Extract first { ... } via regex
    """
    raw = raw.strip()

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try regex: find first outermost JSON object
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法解析 LLM 返回的 JSON 响应: {raw[:500]}")


def _dict_to_result(data: dict, provider: str, model: str, tokens_used: int) -> LLMReviewResult:
    """Convert parsed dict to LLMReviewResult, with defensive defaults."""
    signal_reviews = []
    for sr in data.get("signal_reviews", []):
        signal_reviews.append(SignalReview(
            check_id=sr.get("check_id", ""),
            is_true_positive=sr.get("is_true_positive", True),
            reasoning=sr.get("reasoning", ""),
            severity=sr.get("severity", "medium"),
        ))

    return LLMReviewResult(
        overall_assessment=data.get("overall_assessment", "LLM 未返回综合判断"),
        severity_score=max(0, min(10, data.get("severity_score", 5))),
        signal_reviews=signal_reviews,
        pubpeer_draft=data.get("pubpeer_draft", ""),
        tokens_used=tokens_used,
        provider=provider,
        model=model,
    )


# ---------------------------------------------------------------------------
# DeepSeek provider (default)
# ---------------------------------------------------------------------------

def _call_deepseek(user_prompt: str, config: Config) -> LLMReviewResult:
    """Call DeepSeek API via OpenAI-compatible interface."""
    from openai import OpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "未设置 DEEPSEEK_API_KEY 环境变量。"
            "请运行: export DEEPSEEK_API_KEY=sk-..."
        )

    model = config.deepseek_model or "deepseek-chat"
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.05,
        max_tokens=8192,
    )

    raw = response.choices[0].message.content or ""
    tokens = response.usage.total_tokens if response.usage else 0
    data = _parse_json_response(raw)
    return _dict_to_result(data, "deepseek", model, tokens)


# ---------------------------------------------------------------------------
# Anthropic provider (backup)
# ---------------------------------------------------------------------------

_ANTHROPIC_REVIEW_TOOL = {
    "name": "submit_review",
    "description": "提交学术论文造假审查结论",
    "input_schema": {
        "type": "object",
        "properties": {
            "overall_assessment": {
                "type": "string",
                "description": "综合中文判断，1-2 段，总结关键发现和整体可信度评估",
            },
            "severity_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "造假嫌疑严重程度评分：0-2基本可信，3-4有疑点，5-6值得关注，7-8高度怀疑，9-10铁证",
            },
            "signal_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "check_id": {"type": "string"},
                        "is_true_positive": {"type": "boolean"},
                        "reasoning": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["high", "medium", "low", "false_alarm"],
                        },
                    },
                    "required": ["check_id", "is_true_positive", "reasoning", "severity"],
                },
            },
            "pubpeer_draft": {
                "type": "string",
                "description": "可直接发表在 PubPeer 上的中文审稿意见草稿，需引用具体证据",
            },
        },
        "required": ["overall_assessment", "severity_score", "signal_reviews", "pubpeer_draft"],
    },
}


def _call_anthropic(user_prompt: str, config: Config) -> LLMReviewResult:
    """Call Anthropic API with Tool Use for guaranteed JSON output."""
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "未设置 ANTHROPIC_API_KEY 环境变量。"
            "请运行: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    model = config.openai_model or "claude-sonnet-4-6"
    client = Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[_ANTHROPIC_REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "submit_review"},
    )

    # Extract tool use result
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_review":
            tokens = response.usage.input_tokens + response.usage.output_tokens
            return _dict_to_result(block.input, "anthropic", model, tokens)

    # Fallback: try text content
    raw = response.content[0].text if response.content else ""
    tokens = response.usage.input_tokens + response.usage.output_tokens
    data = _parse_json_response(raw)
    return _dict_to_result(data, "anthropic", model, tokens)


# ---------------------------------------------------------------------------
# OpenAI provider (backup)
# ---------------------------------------------------------------------------

def _call_openai(user_prompt: str, config: Config) -> LLMReviewResult:
    """Call OpenAI API with JSON mode."""
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "未设置 OPENAI_API_KEY 环境变量。"
            "请运行: export OPENAI_API_KEY=sk-..."
        )

    model = config.openai_model or "gpt-4o"
    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.05,
        max_tokens=8192,
    )

    raw = response.choices[0].message.content or ""
    tokens = response.usage.total_tokens if response.usage else 0
    data = _parse_json_response(raw)
    return _dict_to_result(data, "openai", model, tokens)
