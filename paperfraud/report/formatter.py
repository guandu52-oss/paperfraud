"""Report formatters — JSON and Markdown output."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from paperfraud.base import CheckResult


class _NumpyEncoder(json.JSONEncoder):
    """Handle numpy scalar types that aren't natively JSON-serializable."""
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def format_terminal(aggregated: dict, results: list[CheckResult]) -> str:
    """Format results for rich terminal output (handled by rich in CLI)."""
    return ""  # Terminal formatting handled by rich.Live/rich.Table in CLI


def format_json(aggregated: dict, results: list[CheckResult], paper=None, image_artifacts: dict[str, list[str]] | None = None, figure_captions: dict[str, dict] | None = None) -> str:
    """Format results as JSON."""
    summary: dict[str, Any] = {
        "overall_level": aggregated["overall_level"],
        "overall_verdict": aggregated["overall_verdict"],
        "red_count": aggregated["red_count"],
        "orange_count": aggregated["orange_count"],
        "yellow_count": aggregated["yellow_count"],
        "green_count": aggregated["green_count"],
        "error_count": aggregated["error_count"],
        "total_checks": aggregated["total_checks"],
        "needs_human_count": aggregated["needs_human_count"],
        "risk_score": round(aggregated.get("risk_score", 0), 1),
    }
    risk_breakdown = aggregated.get("risk_breakdown")
    if risk_breakdown:
        summary["risk_breakdown"] = risk_breakdown
    if paper is not None:
        summary["title"] = paper.title or ""
        summary["journal"] = paper.journal or ""
        summary["year"] = paper.year
        if paper.authors:
            summary["authors"] = paper.authors

    output: dict[str, Any] = {
        "summary": summary,
        "checks": [r.to_dict() for r in results],
    }
    if "llm_review" in aggregated:
        output["llm_review"] = aggregated["llm_review"]
    if image_artifacts:
        output["images"] = image_artifacts
    if figure_captions:
        output["figure_captions"] = figure_captions
    return json.dumps(output, indent=2, ensure_ascii=False, cls=_NumpyEncoder)


def format_markdown(aggregated: dict, results: list[CheckResult], title: str = "") -> str:
    """Format results as Markdown report."""
    lines = [
        f"# Paper Fraud Detection Report",
        "",
    ]
    if title:
        lines.append(f"**论文：** {title}")
    lines.append("")

    # Summary
    lines.append("## 摘要")
    lines.append("")
    lines.append(f"**综合判定：** {aggregated['overall_verdict']}")
    lines.append("")

    level_emoji = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢", "error": "⚪"}
    lines.append("| 级别 | 数量 |")
    lines.append("|------|------|")
    lines.append(f"| 🔴 Red | {aggregated['red_count']} |")
    lines.append(f"| 🟠 Orange | {aggregated['orange_count']} |")
    lines.append(f"| 🟡 Yellow | {aggregated['yellow_count']} |")
    lines.append(f"| 🟢 Green | {aggregated['green_count']} |")
    lines.append(f"| ⚪ Error | {aggregated['error_count']} |")
    lines.append(f"| **总计** | **{aggregated['total_checks']}** |")
    lines.append("")

    # Risk Score
    risk_score = aggregated.get("risk_score", 0)
    lines.append(f"## 欺诈风险评分: {risk_score:.0f}/100")
    lines.append("")
    risk_breakdown = aggregated.get("risk_breakdown", {})
    if risk_breakdown.get("veto_triggered"):
        lines.append("**一票否决触发：检测到数学铁证**")
        lines.append("")
    if risk_breakdown.get("cluster_bonus", 0) > 0:
        lines.append(f"聚类加分: +{risk_breakdown['cluster_bonus']}")
        lines.append("")
    contributions = risk_breakdown.get("contributions", [])
    if contributions:
        lines.append("| 检测项 | 级别 | 权重 | 相关性 | 贡献分 |")
        lines.append("|--------|------|------|--------|--------|")
        for c in contributions:
            lines.append(
                f"| {c['check_name']} | {c['level']} | {c['weight']:.1f} | "
                f"{c['correlation']:.1f} | {c['contribution']:.1f} |"
            )
        lines.append("")

    if aggregated["needs_human"]:
        lines.append(f"**{aggregated['needs_human_count']} 项需要人工复核**")
        lines.append("")

    # Detail by level (red/orange/yellow first)
    for level in ["red", "orange", "yellow", "green", "error"]:
        level_results = [r for r in results if r.level == level]
        if not level_results:
            continue

        lines.append(f"## {level_emoji[level]} {level.upper()} 信号")
        lines.append("")

        for r in level_results:
            lines.append(f"### {r.check_name}")
            lines.append(f"- **判定：** {r.verdict}")
            if r.confidence < 1.0:
                lines.append(f"- **置信度：** {r.confidence:.0%}")
            if r.evidence:
                lines.append("- **证据：**")
                for e in r.evidence[:5]:
                    lines.append(f"  - {e}")
            if r.needs_human:
                lines.append(f"- **人工复核指引：** {r.human_instruction}")
            lines.append("")

    # LLM Review
    llm_review = aggregated.get("llm_review")
    if llm_review:
        lines.append("## LLM 定性审查")
        lines.append("")
        lines.append(f"**综合判断：** {llm_review.get('overall_assessment', '')}")
        lines.append("")
        lines.append(f"**造假嫌疑评分：** {llm_review.get('severity_score', 0)}/10")
        lines.append("")

        signal_reviews = llm_review.get("signal_reviews", [])
        if signal_reviews:
            lines.append("### 逐信号审查")
            lines.append("")
            lines.append("| 检测项 | 判定 | 严重程度 | 理由 |")
            lines.append("|--------|------|----------|------|")
            for sr in signal_reviews:
                verdict = "真锤" if sr.get("is_true_positive") else "假阳性"
                lines.append(
                    f"| {sr.get('check_id', '')} | {verdict} | {sr.get('severity', '')} | "
                    f"{sr.get('reasoning', '')[:100]} |"
                )
            lines.append("")

        pubpeer = llm_review.get("pubpeer_draft", "")
        if pubpeer:
            lines.append("### PubPeer 审稿草稿")
            lines.append("")
            lines.append(pubpeer)
            lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append("*本报告由 paperfraud-detect 自动生成。所有信号仅供参考，最终判断需人工复核。*")
    lines.append("")
    lines.append("**检测边界声明：** 本工具仅分析已发表论文的公开信息，无法检测以下造假手段：")
    lines.append("- 内部统计自洽的完全伪造数据")
    lines.append("- 论文工厂专业出品（无拼接、无复用）")
    lines.append("- 未公开代码或数据的论文")
    lines.append("- 原始仪器文件")

    return "\n".join(lines)
