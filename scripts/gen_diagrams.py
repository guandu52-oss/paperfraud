"""Generate architecture diagrams for the case study document."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.font_manager as fm
import numpy as np

# Use macOS CJK font
_font_path = "/System/Library/Fonts/STHeiti Medium.ttc"
_fm_prop = fm.FontProperties(fname=_font_path)
matplotlib.rcParams["font.family"] = _fm_prop.get_name()
matplotlib.rcParams["font.sans-serif"] = [_fm_prop.get_name()]
matplotlib.rcParams["axes.unicode_minus"] = False

OUT = "docs/images"
DPI = 150
FONT_FAMILY = _fm_prop.get_name()
BG = "#FAFAFA"
COLORS = {
    "red": "#E74C3C",
    "orange": "#F39C12",
    "green": "#27AE60",
    "blue": "#2980B9",
    "dark": "#2C3E50",
    "gray": "#7F8C8D",
    "light": "#ECF0F1",
    "white": "#FFFFFF",
}


def _draw_box(ax, x, y, w, h, text, color, fontsize=10, text_color="white", bold=False):
    """Draw a rounded box with centered text."""
    box = FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.15", linewidth=1.2,
        edgecolor=color, facecolor=color, alpha=0.92
    )
    ax.add_patch(box)
    weight = "bold" if bold else "normal"
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fontsize, color=text_color, weight=weight, family=FONT_FAMILY)


def _draw_arrow(ax, x1, y1, x2, y2, color="#7F8C8D", lw=1.5):
    """Draw an arrow from (x1, y1) to (x2, y2)."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw,
                                connectionstyle="arc3,rad=0"))


def _setup_figure(figsize=(10, 5)):
    fig, ax = plt.subplots(figsize=figsize, facecolor=BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.axis("off")
    return fig, ax


def gen_three_layer():
    """Section 1.1: Three-layer architecture."""
    fig, ax = _setup_figure((8, 5.5))
    ax.set_title("三层交叉校验引擎", fontsize=16, weight="bold", color=COLORS["dark"], pad=15)

    layers = [
        (1.5, 3.6, 7.0, 1.0, "Layer 3: LLM 定性审查\nDeepSeek / Claude / GPT\n→ 语义理解 & 逻辑矛盾发现", COLORS["red"]),
        (1.5, 2.3, 7.0, 1.0, "Layer 2: 统计分析\nBenford · GRIM · P-curve · 效应量\n→ 数值分布异常检测", COLORS["orange"]),
        (1.5, 1.0, 7.0, 1.0, "Layer 1: 规则引擎\n科学常识硬编码 (n<3 无法统计, WB 条带, 样本量校验)\n→ 硬核物理拦截", COLORS["green"]),
    ]
    for x, y, w, h, text, color in layers:
        _draw_box(ax, x, y, w, h, text, color, fontsize=10)

    # Arrows between layers
    _draw_arrow(ax, 5.0, 3.6, 5.0, 3.35, COLORS["gray"])
    _draw_arrow(ax, 5.0, 2.3, 5.0, 2.05, COLORS["gray"])

    # Side labels
    ax.text(9.0, 4.1, "上层\n高精度", ha="center", fontsize=8, color=COLORS["gray"])
    ax.text(9.0, 2.8, "中层\n统计验证", ha="center", fontsize=8, color=COLORS["gray"])
    ax.text(9.0, 1.5, "底层\n兜底保障", ha="center", fontsize=8, color=COLORS["gray"])
    ax.plot([8.5, 8.7], [4.1, 4.1], color=COLORS["gray"], lw=0.5)
    ax.plot([8.5, 8.7], [2.8, 2.8], color=COLORS["gray"], lw=0.5)
    ax.plot([8.5, 8.7], [1.5, 1.5], color=COLORS["gray"], lw=0.5)

    fig.tight_layout()
    fig.savefig(f"{OUT}/three-layer-architecture.png", dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def gen_human_in_loop():
    """Section 1.2: Human-in-the-Loop flow."""
    fig, ax = _setup_figure((10, 3.5))
    ax.set_title("图像取证流程：Human-in-the-Loop", fontsize=16, weight="bold", color=COLORS["dark"], pad=15)

    # Flow boxes
    boxes = [
        (0.3, 1.2, 1.6, 0.8, "PDF\n图片提取", COLORS["blue"]),
        (2.5, 1.2, 1.8, 0.8, "ELA 热力图\nLUT 伪彩图\nClone 标记", COLORS["dark"]),
        (5.0, 1.2, 2.0, 0.8, "Web UI\n审查工作台\n(双图对比+放大镜)", COLORS["orange"]),
        (7.8, 1.2, 1.6, 0.8, "人工判定\n写入结论", COLORS["red"]),
    ]
    for x, y, w, h, text, color in boxes:
        _draw_box(ax, x, y, w, h, text, color, fontsize=9)

    _draw_arrow(ax, 1.9, 1.6, 2.45, 1.6, COLORS["gray"])
    _draw_arrow(ax, 4.35, 1.6, 4.95, 1.6, COLORS["gray"])
    _draw_arrow(ax, 7.05, 1.6, 7.75, 1.6, COLORS["gray"])

    # Bottom note
    ax.text(5.0, 0.4, "系统仅标出疑点 → 最终定案由人类专家完成\n保证学术风控程序的正当性与严肃性",
            ha="center", fontsize=9, color=COLORS["gray"], style="italic")

    fig.tight_layout()
    fig.savefig(f"{OUT}/human-in-the-loop.png", dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def gen_threat_intel():
    """Section 3.1: Threat intelligence pipeline."""
    fig, ax = _setup_figure((10, 5))
    ax.set_title("威胁情报网络：数据飞轮", fontsize=16, weight="bold", color=COLORS["dark"], pad=15)

    # Sources
    _draw_box(ax, 0.3, 3.2, 2.0, 0.8, "PubPeer API\n(CSRF 认证)", COLORS["blue"])
    _draw_box(ax, 0.3, 2.0, 2.0, 0.8, "FBS RSS\n(XML 解析+去重)", COLORS["blue"])

    # Processing
    _draw_box(ax, 3.0, 2.6, 2.0, 1.4, "Crawl → Store\nSQLite FTS5\n全文索引", COLORS["dark"])
    _draw_arrow(ax, 2.35, 3.6, 2.95, 3.4, COLORS["gray"])
    _draw_arrow(ax, 2.35, 2.4, 2.95, 2.6, COLORS["gray"])

    # LLM
    _draw_box(ax, 5.7, 2.6, 1.8, 1.4, "LLM 提取\n(DeepSeek)\n24 条模式", COLORS["orange"])
    _draw_arrow(ax, 5.05, 3.3, 5.65, 3.3, COLORS["gray"])

    # Output
    _draw_box(ax, 8.0, 3.2, 1.6, 0.8, "YAML\n黑名单规则", COLORS["red"])
    _draw_box(ax, 8.0, 2.0, 1.6, 0.8, "LLM Prompt\n句式模板", COLORS["orange"])
    _draw_arrow(ax, 7.55, 3.6, 7.95, 3.6, COLORS["gray"])
    _draw_arrow(ax, 7.55, 2.6, 7.95, 2.4, COLORS["gray"])

    # Feedback loop
    ax.annotate("", xy=(0.8, 1.2), xytext=(8.3, 1.2),
                arrowprops=dict(arrowstyle="->", color=COLORS["green"], lw=2,
                                connectionstyle="arc3,rad=-0.4"))
    ax.text(4.8, 0.7, "社区暴露 → AI 提取 → 人工确认 → 引擎进化", ha="center",
            fontsize=11, weight="bold", color=COLORS["green"])

    fig.tight_layout()
    fig.savefig(f"{OUT}/threat-intel-pipeline.png", dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


def gen_four_layer_filter():
    """Section 2.3: Four-layer filtering mechanism."""
    fig, ax = _setup_figure((9, 6))
    ax.set_title("四层过滤机制：262 → 13 误报治理", fontsize=16, weight="bold", color=COLORS["dark"], pad=15)

    layers = [
        (1.0, 3.8, 7.0, 0.9, "Layer 1: P 值过滤 — 跳过 < 1e-10 的统计输出值 (如 8.8e-14)", COLORS["green"]),
        (1.0, 2.7, 7.0, 0.9, "Layer 2: 参数列检测 — 识别列内几何/等差序列 (如 300→100→33.3 ÷3 稀释)", COLORS["blue"]),
        (1.0, 1.6, 7.0, 0.9, "Layer 3: 参数行检测 — 识别行内几何/等差序列 (如 5.4→10.8→21.6 ×2 浓度)", COLORS["orange"]),
        (1.0, 0.5, 7.0, 0.9, "Layer 4: 模板重复检测 — 组学数据中 >60% 共享同一频次 → 判定为模板结构", COLORS["red"]),
    ]
    for x, y, w, h, text, color in layers:
        _draw_box(ax, x, y, w, h, text, color, fontsize=9.5)

    # Arrows
    for i in range(3):
        _draw_arrow(ax, 5.0, 3.8 - i * 1.1, 5.0, 3.65 - i * 1.1, COLORS["gray"])

    # Side metrics
    ax.text(8.5, 4.25, "输入\n262 警报", ha="center", fontsize=8, color=COLORS["gray"])
    ax.text(8.5, 0.95, "输出\n13 精准信号", ha="center", fontsize=9, weight="bold", color=COLORS["red"])

    fig.tight_layout()
    fig.savefig(f"{OUT}/four-layer-filter.png", dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)


if __name__ == "__main__":
    gen_three_layer()
    gen_human_in_loop()
    gen_four_layer_filter()
    gen_threat_intel()
    print("Done: 4 diagrams generated in docs/images/")
