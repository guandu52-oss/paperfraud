"""Streamlit dashboard for paperfraud-detect forensic reports.

Usage:
    streamlit run paperfraud/web/app.py
    streamlit run paperfraud/web/app.py -- --report path/to/report.json
    http://localhost:8501/?report=path/to/report.json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Paper Fraud Detection",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Resolve report path ──────────────────────────────────────────────────────
_report_file: Path | None = None


def _resolve(p: Path) -> Path | None:
    """Accept a path to report.json or a directory containing one."""
    if p.is_dir():
        candidate = p / "report.json"
        return candidate if candidate.exists() else None
    return p if p.exists() else None


# Priority 1: CLI -- --report or PAPERFRAUD_REPORT_PATH env var
_cli_arg: str | None = None
try:
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--report" and i + 1 < len(argv):
            _cli_arg = argv[i + 1]
            break
except Exception:
    pass
if not _cli_arg:
    _cli_arg = os.environ.get("PAPERFRAUD_REPORT_PATH", "") or None

if _cli_arg:
    _report_file = _resolve(Path(_cli_arg))
    if _report_file is None:
        st.error(f"报告未找到: {_cli_arg}")
        st.stop()

# Priority 2: URL query param ?report=...
if _report_file is None:
    qp = st.query_params.get("report")
    if qp:
        _report_file = _resolve(Path(qp))
        if _report_file is None:
            st.error(f"报告未找到: {qp}")
            st.stop()

# ── Landing page (no report loaded) ──────────────────────────────────────────
if _report_file is None:
    st.title("🔬 Paper Fraud Dashboard")
    st.markdown("### 打开一份检测报告")

    st.markdown(
        "**方式 1：URL 参数**  \n"
        "在地址栏输入：`http://localhost:8501/?report=<路径>`  \n"
        "例如：`http://localhost:8501/?report=paperfraud_output/test/report.json`"
    )
    st.markdown("---")
    st.markdown("**方式 2：直接输入路径**")

    col1, col2 = st.columns([3, 1])
    with col1:
        path_input = st.text_input(
            "报告 JSON 路径或目录",
            placeholder="paperfraud_output/test/report.json",
            label_visibility="collapsed",
        )
    with col2:
        uploaded = st.file_uploader("或上传 JSON", type=["json"], label_visibility="collapsed")

    if st.button("打开报告", type="primary") and path_input:
        resolved = _resolve(Path(path_input.strip()))
        if resolved:
            st.query_params["report"] = str(resolved)
            st.stop()
        else:
            st.error(f"文件不存在: {path_input.strip()}")

    if uploaded is not None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.write(uploaded.read())
        tmp.close()
        st.query_params["report"] = str(Path(tmp.name))
        st.stop()

    st.info("请输入报告路径、上传文件，或使用 URL 参数。")
    st.stop()

# ── Load report JSON ─────────────────────────────────────────────────────────
try:
    report: dict[str, Any] = json.loads(_report_file.read_text(encoding="utf-8"))
except Exception as e:
    st.error(f"无法解析报告 JSON: {e}")
    st.stop()

report_dir = _report_file.parent
summary = report.get("summary", {})
checks = report.get("checks", [])
llm_review = report.get("llm_review")
images_meta = report.get("images", {})
figure_captions: dict[str, dict] = report.get("figure_captions", {})

# ── Helpers ──────────────────────────────────────────────────────────────────

LEVEL_COLORS = {
    "red": "#dc3545", "orange": "#fd7e14", "yellow": "#ffc107",
    "green": "#28a745", "error": "#6c757d",
}
LEVEL_BG = {
    "red": "#f8d7da", "orange": "#fff3cd", "yellow": "#fff3cd",
    "green": "#d4edda", "error": "#e2e3e5",
}
LEVEL_BORDER = {
    "red": "#dc3545", "orange": "#fd7e14", "yellow": "#ffc107",
    "green": "#28a745", "error": "#6c757d",
}
LEVEL_EMOJI = {
    "red": "🚨", "orange": "⚠️", "yellow": "⚡", "green": "✅", "error": "❓",
}


def _verdict_banner(level: str, verdict: str) -> None:
    bg = LEVEL_BG.get(level, "#e2e3e5")
    border = LEVEL_BORDER.get(level, "#6c757d")
    emoji = LEVEL_EMOJI.get(level, "")
    st.markdown(
        f"""<div style="background:{bg};border-left:6px solid {border};
        padding:1.2rem 1.5rem;border-radius:6px;margin:0.5rem 0 1.5rem 0;">
        <h2 style="margin:0;color:#333;">{emoji} {verdict}</h2></div>""",
        unsafe_allow_html=True,
    )


def _stat_cards(summary: dict) -> None:
    cols = st.columns(5)
    items = [
        ("Red", summary.get("red_count", 0), "#dc3545"),
        ("Orange", summary.get("orange_count", 0), "#fd7e14"),
        ("Yellow", summary.get("yellow_count", 0), "#ffc107"),
        ("Green", summary.get("green_count", 0), "#28a745"),
        ("Error", summary.get("error_count", 0), "#6c757d"),
    ]
    for col, (label, count, color) in zip(cols, items):
        with col:
            st.markdown(
                f"""<div style="text-align:center;padding:1rem 0.5rem;
                border-radius:8px;background:{color}15;border:2px solid {color}40;">
                <div style="font-size:2rem;font-weight:700;color:{color};">{count}</div>
                <div style="font-size:0.85rem;color:#666;">{label}</div></div>""",
                unsafe_allow_html=True,
            )


def _signal_card(check: dict, expanded: bool = False) -> None:
    level = check.get("level", "error")
    name = check.get("check_name", check.get("check_id", "Unknown"))
    verdict = check.get("verdict", "")
    confidence = check.get("confidence", 1.0)
    evidence = check.get("evidence", [])
    human_instruction = check.get("human_instruction", "")

    with st.expander(f"{name} — {verdict[:80]}", expanded=expanded):
        st.markdown(f"**判定：** {verdict}")
        if confidence < 1.0:
            st.caption(f"置信度：{confidence:.0%}")
        if evidence:
            st.markdown("**证据：**")
            for e in evidence[:10]:
                st.markdown(f"- {e}")
        if human_instruction:
            st.info(human_instruction)


# ── Image Viewer ─────────────────────────────────────────────────────────────

import re

from streamlit.components.v1 import declare_component

from paperfraud.web.image_utils import (
    get_output_subdirs,
    load_image_for_web,
)

# Custom component: interactive image region selector (Canvas with draw+drag)
_image_selector_component = declare_component(
    "image_selector",
    path=Path(__file__).parent / "image_selector",
)

KNOWN_SUBDIRS = {
    "lut": "lut_output",
    "clone": "clone_output",
    "ela": "ela_output",
}
TYPE_LABELS = {
    "lut": "LUT 伪彩映射",
    "clone": "克隆区域检测",
    "ela": "ELA 误差水平分析",
}
TYPE_HELP = {
    "lut": (
        "**LUT (Look-Up Table) 伪彩映射**：将图片转为三种科学伪彩色（Fire/Iron、Royal、Viridis），"
        "用颜色差异放大肉眼难以察觉的编辑痕迹。拼接/修图区域在伪彩色下会呈现不自然的颜色断层。"
        "\n\n用法：对比三列伪彩色图，寻找颜色过渡不自然的矩形或块状区域。"
    ),
    "clone": (
        "**克隆区域检测**：用算法检测图片中是否存在被复制粘贴的相同区域（克隆/复制-移动伪造）。"
        "红色连线连接的两个区域在像素级别高度相似，可能是一个区域的 copy-paste。"
        "\n\n用法：红色连线密集且连接两个语义上不应相同的区域（如两条本该不同的 WB 条带）即为可疑。"
    ),
    "ela": (
        "**ELA (Error Level Analysis) 误差水平分析**：利用 JPEG 压缩原理——不同来源的图片经过不同次数的"
        "重压缩，误差水平不一致。将图片以固定质量重新保存后，对比原图差异，差异大的区域（红/黄高亮）"
        "可能来自外部粘贴。\n\n用法：关注 Western Blot 条带周围、显微照片内部的规则形状高亮区块。"
        "\n\n注意：JPEG 8×8 块边界和 PDF 文本渲染天然存在压缩差异，会形成假阳性。"
    ),
}


def _natural_key(name: str) -> tuple[int, str]:
    """Sort key for page-based names: page2 < page4 < page11."""
    m = re.match(r"page(\d+)(?:_(\w+))?", name)
    if m:
        return (int(m.group(1)), m.group(2) or "")
    return (0, name)


def _image_label(name: str, stems: list[str] | None = None) -> str:
    """Label for page-based figure names.

    'page2_1' (sole)  → '第 2 页 — caption'
    'page5_1', 'page5_2' (multiple) → '第 5 页 · 图 1', '第 5 页 · 图 2'
    """
    m = re.match(r"page(\d+)(?:_(\d+))?$", name)
    if m:
        page = m.group(1)
        idx = m.group(2)
        # Determine if this page has multiple figure clusters
        multi = False
        if stems:
            prefix = f"page{page}_"
            multi = sum(1 for s in stems if s.startswith(prefix)) > 1
        # Caption hint from CLI figure_captions
        fig_info = figure_captions.get(page, {})
        caption_hint = ""
        if fig_info:
            caption = fig_info.get("caption", "")
            short = caption[:80] + "…" if len(caption) > 80 else caption
            caption_hint = f" — {short}"
        if multi and idx:
            return f"第 {page} 页 · 图 {idx}{caption_hint}"
        return f"第 {page} 页{caption_hint}"
    return name


def _render_image_viewer() -> None:
    st.subheader("🖼️ 图像取证")

    subdirs = {k: report_dir / v for k, v in KNOWN_SUBDIRS.items() if (report_dir / v).is_dir()}
    if not subdirs:
        st.info("未找到图像取证输出。请使用 `--extract-images` 重新运行检测。")
        return

    image_type = st.selectbox(
        "选择取证类型",
        list(subdirs.keys()),
        format_func=lambda x: TYPE_LABELS.get(x, x),
    )

    # Show explanation for the selected type
    if image_type in TYPE_HELP:
        with st.expander(f"📖 什么是{TYPE_LABELS[image_type]}？", expanded=False):
            st.markdown(TYPE_HELP[image_type])

    if image_type == "lut":
        _lut_viewer(subdirs["lut"])
    elif image_type == "clone":
        _clone_viewer(subdirs["clone"])
    elif image_type == "ela":
        _ela_viewer(subdirs["ela"])


def _find_original(forensic_name: str, suffix: str) -> Path | None:
    """Try to find original image for a forensic output.

    Forensic images are named like `page1_img0_ela.png`. The original
    extracted image would be `page1_img0.png` in the report_dir/images/ dir.
    """
    images_dir = report_dir / "images"
    if not images_dir.is_dir():
        return None
    # Strip suffix: page1_img0_ela → page1_img0
    stem = forensic_name.rsplit("_" + suffix, 1)[0]
    for ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
        candidate = images_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _crop_image(img, x_pct: float, y_pct: float, w_pct: float, h_pct: float):
    """Crop a PIL image to the given percentage region."""
    w, h = img.size
    left = int(w * x_pct / 100)
    top = int(h * y_pct / 100)
    right = int(w * (x_pct + w_pct) / 100)
    bottom = int(h * (y_pct + h_pct) / 100)
    left, top = max(0, left), max(0, top)
    right, bottom = min(w, right), min(h, bottom)
    if right <= left or bottom <= top:
        return img
    return img.crop((left, top, right, bottom))


def _image_selector(ref_path: Path, key: str):
    """Interactive canvas widget for selecting a crop region by drawing a box.

    Uses declare_component (not st.components.v1.html) for proper bidirectional
    communication. The component is keyed by ref_path so Streamlit destroys and
    recreates the iframe when the user switches images — auto-clearing the crop.
    Coordinates are only sent on mouseup (not mousemove) to avoid excessive
    Python-side rerenders.

    Returns (x, y, w, h) in percentages, or None.
    """
    import base64
    from io import BytesIO

    from PIL import Image

    # Load + resize reference image for canvas (max 700px wide)
    img = Image.open(ref_path)
    max_w = 700
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    result = _image_selector_component(
        image_data_url=data_url,
        key=str(ref_path),
        default=None,
    )

    if isinstance(result, dict) and all(k in result for k in ("x", "y", "w", "h")):
        w = float(result["w"])
        h = float(result["h"])
        if w > 0 and h > 0:
            st.session_state[f"{key}_crop"] = (
                float(result["x"]), float(result["y"]), w, h,
            )
            return st.session_state[f"{key}_crop"]

    # Component returned None (no crop / cleared / switched image)
    st.session_state.pop(f"{key}_crop", None)
    return None


def _lut_viewer(lut_dir: Path) -> None:
    files = sorted(lut_dir.glob("*.png"), key=lambda f: _natural_key(f.name))
    if not files:
        st.info("未发现 LUT 图片。")
        return

    stems = sorted(
        set(
            f.stem.rsplit("_", 1)[0] for f in files
            if any(f.stem.endswith(f"_{s}") for s in ("fire", "royal", "viridis"))
        ),
        key=_natural_key,
    ) or sorted((f.stem for f in files), key=_natural_key)

    selected = st.selectbox(
        "选择图片",
        stems,
        format_func=lambda x: _image_label(x, stems),
    )
    fire_p = lut_dir / f"{selected}_fire.png"
    royal_p = lut_dir / f"{selected}_royal.png"
    viridis_p = lut_dir / f"{selected}_viridis.png"

    original = _find_original(selected, "fire") or _find_original(selected, "royal")
    ref_img = original if original else (fire_p if fire_p.exists() else (royal_p if royal_p.exists() else viridis_p))

    st.caption("🖱️ 在下方原图上**拖拽画框**选择放大区域，还可**拖动已选框**，**双击**清除")
    crop = _image_selector(ref_img, "lut")

    # Row 1: 原始图片 | Fire/Iron
    row1_cols = st.columns(2)
    with row1_cols[0]:
        st.caption("原始图片" if original else "参考图")
        if original:
            img = load_image_for_web(original)
            st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
        else:
            ref = load_image_for_web(ref_img)
            st.image(_crop_image(ref, *crop) if crop else ref, use_container_width=True)
        if original:
            with open(original, "rb") as f:
                st.download_button("📥 原图", f.read(), original.name, key="dl_orig_lut")

    with row1_cols[1]:
        st.caption("Fire/Iron")
        if fire_p.exists():
            img = load_image_for_web(fire_p)
            st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            with open(fire_p, "rb") as f:
                st.download_button("📥 Fire", f.read(), fire_p.name, key=f"dl_fire_{selected}")
        else:
            st.caption("—")

    # Row 2: Royal | Viridis
    row2_cols = st.columns(2)
    with row2_cols[0]:
        st.caption("Royal")
        if royal_p.exists():
            img = load_image_for_web(royal_p)
            st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            with open(royal_p, "rb") as f:
                st.download_button("📥 Royal", f.read(), royal_p.name, key=f"dl_royal_{selected}")
        else:
            st.caption("—")

    with row2_cols[1]:
        st.caption("Viridis")
        if viridis_p.exists():
            img = load_image_for_web(viridis_p)
            st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            with open(viridis_p, "rb") as f:
                st.download_button("📥 Viridis", f.read(), viridis_p.name, key=f"dl_viridis_{selected}")
        else:
            st.caption("—")


def _clone_viewer(clone_dir: Path) -> None:
    files = sorted(clone_dir.glob("*_clone.png"), key=lambda f: _natural_key(f.name))
    if not files:
        st.info("未发现克隆标记图。")
        return

    # Build filtered filename list using stem-based filtering
    filenames = [f.name for f in files]
    stems = [f.replace("_clone.png", "") for f in filenames]

    selected = st.selectbox(
        "选择克隆标记图",
        filenames,
        format_func=lambda x: _image_label(x.replace("_clone.png", ""), stems),
    )
    if selected:
        path = clone_dir / selected
        original = _find_original(selected, "clone")
        ref_img = original if original else path

        st.caption("🖱️ 在下方原图上**拖拽画框**选择放大区域，还可**拖动已选框**，**双击**清除")
        crop = _image_selector(ref_img, "clone")

        cols = st.columns(2 if original else 1)
        col_idx = 0
        if original:
            with cols[0]:
                st.caption("原始图片")
                img = load_image_for_web(original)
                st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            col_idx = 1
        with cols[col_idx]:
            st.caption("克隆标记（红色连线 = 相同区域）")
            img = load_image_for_web(path)
            st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            with open(path, "rb") as f:
                st.download_button("📥 下载标记图", f.read(), selected, key="dl_clone")


def _ela_viewer(ela_dir: Path) -> None:
    files = sorted(ela_dir.glob("*_ela.png"), key=lambda f: _natural_key(f.name))
    if not files:
        st.info("未发现 ELA 热力图。")
        return

    filenames = [f.name for f in files]
    stems = [f.replace("_ela.png", "") for f in filenames]

    selected = st.selectbox(
        "选择 ELA 热力图",
        filenames,
        format_func=lambda x: _image_label(x.replace("_ela.png", ""), stems),
    )
    if selected:
        path = ela_dir / selected
        original = _find_original(selected, "ela")
        ref_img = original if original else path

        st.caption("🖱️ 在下方原图上**拖拽画框**选择放大区域，还可**拖动已选框**，**双击**清除")
        crop = _image_selector(ref_img, "ela")

        cols = st.columns(2 if original else 1)
        col_idx = 0
        if original:
            with cols[0]:
                st.caption("原始图片")
                img = load_image_for_web(original)
                st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            col_idx = 1
        with cols[col_idx]:
            st.caption("ELA 热力图（红/黄 = 压缩误差异常）")
            img = load_image_for_web(path)
            st.image(_crop_image(img, *crop) if crop else img, use_container_width=True)
            st.caption("高亮区域可能来自不同来源。JPEG 8×8 块边界和 PDF 文本也可能产生差异。")
            with open(path, "rb") as f:
                st.download_button("📥 下载热力图", f.read(), selected, key="dl_ela")


# ── Comparison Viewer (Human-in-the-Loop) ────────────────────────────────────

def _encode_image_base64(img_path: Path) -> str:
    """Read an image file and return a base64 data URL (without the prefix)."""
    import base64
    from io import BytesIO

    from PIL import Image

    img = Image.open(img_path)
    # Single high-quality resize to match the comparison viewer canvas (1500px).
    # This avoids the double-resize artifact: PIL→1500→Canvas(1:1, no second scale).
    max_w = 1500
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _comparison_component(image_a_path: Path, image_b_path: Path):
    """Render the comparison viewer with two images embedded directly in HTML.

    Uses st.components.v1.html to embed the full HTML with images as base64
    data URLs. The image pair key (from filename stems) is embedded in the
    HTML content itself, so Streamlit re-renders when the pair changes.
    """
    data_a = _encode_image_base64(image_a_path)
    data_b = _encode_image_base64(image_b_path)

    # Read the HTML template
    html_path = Path(__file__).parent / "comparison_viewer" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    # Inject image data URLs as JS globals
    html = html.replace("__IMG_A_DATA__", json.dumps(data_a))
    html = html.replace("__IMG_B_DATA__", json.dumps(data_b))

    st.components.v1.html(html, height=1600, scrolling=True)


def _comparison_viewer() -> None:
    st.subheader("🔍 人工审查工作台")

    images_dir = report_dir / "images"
    if not images_dir.is_dir():
        st.info("未找到提取的图片。请使用 `--extract-images` 重新运行检测。")
        return

    stems = sorted(
        {p.stem for p in images_dir.glob("*.png")},
        key=_natural_key,
    )
    if len(stems) < 2:
        st.info("至少需要 2 张图片才能进行比对。")
        return

    col_a, col_b = st.columns(2)
    with col_a:
        img_a_stem = st.selectbox(
            "图片 A",
            stems,
            format_func=lambda x: _image_label(x, stems),
            key="cmp_img_a",
        )
    with col_b:
        default_b_idx = min(1, len(stems) - 1) if len(stems) > 1 else 0
        img_b_stem = st.selectbox(
            "图片 B",
            stems,
            index=default_b_idx,
            format_func=lambda x: _image_label(x, stems),
            key="cmp_img_b",
        )

    if img_a_stem and img_b_stem:
        img_a_path = images_dir / f"{img_a_stem}.png"
        img_b_path = images_dir / f"{img_b_stem}.png"

        if img_a_stem == img_b_stem:
            st.warning("选择了相同的图片，请选择不同的图片进行比对。")
            return

        st.markdown("---")
        st.caption(
            "**Sync 同步放大镜** — 滚轮缩放、拖拽平移，两图同步。"
            " **Blink 闪烁对比** — 原位交替闪烁，拼接边缘最易察觉。"
            " **Diff 差异混合** — 像素级减法热力图。"
            " **LUT 伪彩映射** — 伪彩色放大灰度断层。"
        )

        _comparison_component(
            img_a_path,
            img_b_path,
        )


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🔬 Paper Fraud Detection")

page = st.sidebar.radio("导航", [
    "📊 检测总览",
    "🔴 Red 信号",
    "🟠 Orange 信号",
    "🟡 Yellow 信号",
    "🖼️ 图像取证",
    "🔍 人工审查工作台",
    "🤖 LLM 审查",
    "📝 PubPeer 草稿",
    "📥 导出报告",
])

st.sidebar.markdown("---")
if summary.get("title"):
    st.sidebar.markdown(f"**{summary['title'][:100]}**")
if summary.get("journal"):
    st.sidebar.caption(f"*{summary['journal']}*")
if summary.get("year"):
    st.sidebar.caption(f"Year: {summary['year']}")

# ── Pages ────────────────────────────────────────────────────────────────────

if page == "📊 检测总览":
    _verdict_banner(
        summary.get("overall_level", "error"),
        summary.get("overall_verdict", "无法判定"),
    )
    _stat_cards(summary)

    # Risk level legend
    with st.expander("📖 风险等级说明", expanded=False):
        st.markdown("""
| 等级 | 含义 |
|------|------|
| 🔴 **Red** | 高风险 — 明确的造假/错误信号，通常涉及统计推断谬误、数据矛盾或多图重复等关键证据 |
| 🟠 **Orange** | 中风险 — 存在可疑模式，但可能由正常原因导致（如尾数偏好、数据录入习惯），需人工核实 |
| 🟡 **Yellow** | 低风险 — 轻微异常或数据质量问题，不直接等于造假（如小数位不一致、本福特定律偏差），建议复核 |
| 🟢 **Green** | 通过 — 该项检测未发现异常 |
| ⚪ **Error** | 无法执行 — 数据不足或输入格式不满足检测前提 |
""")

    if summary.get("needs_human_count", 0):
        st.warning(f"⚠️ {summary['needs_human_count']} 项检测需要人工复核")

    LEVEL_EMOJI_MAP = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢", "error": "⚪"}
    st.markdown("### 所有检测项")
    st.dataframe(
        [
            {
                "级别": f"{LEVEL_EMOJI_MAP.get(c.get('level', 'error'), '⚪')} {c.get('level', 'error').upper()}",
                "检测项": c.get("check_name", c.get("check_id", "")),
                "判定": c.get("verdict", "")[:100],
                "置信度": f"{c.get('confidence', 1.0):.0%}",
                "需复核": "是" if c.get("needs_human") else "",
            }
            for c in checks
        ],
        use_container_width=True,
        hide_index=True,
    )

elif page == "🔴 Red 信号":
    red = [c for c in checks if c.get("level") == "red"]
    if not red:
        st.success("没有 Red 级别信号")
    else:
        for c in red:
            _signal_card(c, expanded=True)

elif page == "🟠 Orange 信号":
    orange = [c for c in checks if c.get("level") == "orange"]
    if not orange:
        st.success("没有 Orange 级别信号")
    else:
        for c in orange:
            _signal_card(c, expanded=False)

elif page == "🟡 Yellow 信号":
    yellow = [c for c in checks if c.get("level") == "yellow"]
    if not yellow:
        st.success("没有 Yellow 级别信号")
    else:
        for c in yellow:
            _signal_card(c, expanded=False)

elif page == "🖼️ 图像取证":
    _render_image_viewer()

elif page == "🔍 人工审查工作台":
    _comparison_viewer()

elif page == "🤖 LLM 审查":
    if not llm_review:
        st.info("此报告未包含 LLM 定性审查。请使用 `--review` 重新运行检测。")
    else:
        st.subheader("综合判断")
        st.markdown(llm_review.get("overall_assessment", "无"))
        severity = llm_review.get("severity_score", 0)
        sev_color = "#dc3545" if severity >= 7 else "#fd7e14" if severity >= 4 else "#28a745"
        st.markdown(
            f"""<div style="margin:1rem 0;"><span style="font-weight:600;">造假嫌疑评分：</span>
            <span style="font-size:1.5rem;font-weight:700;color:{sev_color};">{severity}/10</span></div>""",
            unsafe_allow_html=True,
        )
        st.progress(severity / 10)
        if llm_review.get("signal_reviews"):
            st.markdown("### 逐信号审查")
            st.dataframe(
                [
                    {
                        "检测项": sr.get("check_id", ""),
                        "判定": "真锤" if sr.get("is_true_positive") else "假阳性",
                        "严重程度": sr.get("severity", ""),
                        "理由": sr.get("reasoning", "")[:120],
                    }
                    for sr in llm_review["signal_reviews"]
                ],
                use_container_width=True,
                hide_index=True,
            )

elif page == "📝 PubPeer 草稿":
    if llm_review and llm_review.get("pubpeer_draft"):
        st.subheader("PubPeer 审稿草稿")
        draft = llm_review["pubpeer_draft"]
        st.text_area("", draft, height=400, key="pubpeer_display")
        st.download_button("📥 下载草稿", draft, "pubpeer_draft.txt", key="dl_pubpeer")
    else:
        st.info("此报告未包含 LLM 生成的 PubPeer 草稿。")

elif page == "📥 导出报告":
    st.subheader("导出报告")
    from paperfraud.report.formatter import format_markdown
    from paperfraud.base import CheckResult

    results = [
        CheckResult(
            check_id=c.get("check_id", ""),
            check_name=c.get("check_name", ""),
            level=c.get("level", "error"),
            verdict=c.get("verdict", ""),
            evidence=c.get("evidence", []),
            confidence=c.get("confidence", 1.0),
            needs_human=c.get("needs_human", False),
            human_instruction=c.get("human_instruction", ""),
        )
        for c in checks
    ]
    aggregated = {
        "overall_level": summary.get("overall_level", "error"),
        "overall_verdict": summary.get("overall_verdict", ""),
        "red_count": summary.get("red_count", 0),
        "orange_count": summary.get("orange_count", 0),
        "yellow_count": summary.get("yellow_count", 0),
        "green_count": summary.get("green_count", 0),
        "error_count": summary.get("error_count", 0),
        "total_checks": summary.get("total_checks", len(checks)),
        "needs_human_count": summary.get("needs_human_count", 0),
        "needs_human": any(c.get("needs_human") for c in checks),
    }
    if llm_review:
        aggregated["llm_review"] = llm_review

    title = summary.get("title", "Unknown Paper")
    st.download_button(
        "📥 下载 Markdown 报告",
        format_markdown(aggregated, results, title),
        "report.md",
        key="dl_md",
    )
    st.download_button(
        "📥 下载 JSON 报告",
        json.dumps(report, indent=2, ensure_ascii=False),
        "report.json",
        key="dl_json",
    )
