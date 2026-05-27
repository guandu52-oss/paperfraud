"""Streamlit dashboard for paperfraud-detect forensic reports.

Usage:
    streamlit run paperfraud/web/app.py
    streamlit run paperfraud/web/app.py -- --report path/to/report.json
    streamlit run paperfraud/web/app.py -- --reports-dir output/
    http://localhost:8501/?report=path/to/report.json
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# ── Load .env ────────────────────────────────────────────────────────────────
from paperfraud.config import load_dotenv
load_dotenv()

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
_reports_dir: Path | None = None
_available_reports: list[dict[str, Any]] = []  # [{path, title, journal, year}, ...]


def _resolve(p: Path) -> Path | None:
    """Accept a path to report.json or a directory containing one."""
    if p.is_dir():
        candidate = p / "report.json"
        return candidate if candidate.exists() else None
    return p if p.exists() else None


def _scan_reports(base_dir: Path) -> list[dict[str, Any]]:
    """Scan directory for report.json files and extract metadata."""
    reports = []
    for rp in base_dir.rglob("report.json"):
        if len(rp.relative_to(base_dir).parts) > 3:
            continue
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
            summary = data.get("summary", {})
            reports.append({
                "path": str(rp),
                "title": summary.get("title", rp.parent.name) or rp.parent.name,
                "journal": summary.get("journal", ""),
                "year": summary.get("year"),
                "risk_score": summary.get("risk_score", 0),
                "overall_level": summary.get("overall_level", "green"),
            })
        except Exception:
            reports.append({
                "path": str(rp),
                "title": rp.parent.name,
                "journal": "",
                "year": None,
                "risk_score": 0,
                "overall_level": "error",
            })
    reports.sort(key=lambda r: r.get("risk_score", 0), reverse=True)
    return reports


# Priority 1: CLI -- --report / --reports-dir
_cli_arg: str | None = None
_reports_dir_arg: str | None = None
try:
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--report" and i + 1 < len(argv):
            _cli_arg = argv[i + 1]
        if a == "--reports-dir" and i + 1 < len(argv):
            _reports_dir_arg = argv[i + 1]
except Exception:
    pass
if not _cli_arg:
    _cli_arg = os.environ.get("PAPERFRAUD_REPORT_PATH", "") or None

if _reports_dir_arg:
    _reports_dir = Path(_reports_dir_arg)
    if _reports_dir.is_dir():
        _available_reports = _scan_reports(_reports_dir)
    else:
        st.error(f"报告目录不存在: {_reports_dir_arg}")
        _reports_dir = None

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

# ── Single-image forensic helper ──────────────────────────────────────────────


def _generate_image_report(image_path: Path, output_dir: Path) -> Path:
    """Run LUT + ELA + Clone on a single image, generate report.json.

    Returns path to the generated report.json.
    """
    import cv2

    from paperfraud.base import CheckResult
    from paperfraud.checks.images.clone_detect import detect_clones, draw_clone_boxes
    from paperfraud.checks.images.ela import compute_ela
    from paperfraud.checks.images.lut import apply_lut
    from paperfraud.report.aggregator import aggregate_results
    from paperfraud.report.formatter import format_json

    images_dir = output_dir / "images"
    lut_dir = output_dir / "lut_output"
    ela_dir = output_dir / "ela_output"
    clone_dir = output_dir / "clone_output"
    for d in [images_dir, lut_dir, ela_dir, clone_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Copy original to images/
    stem = image_path.stem
    img_dest = images_dir / image_path.name
    if not img_dest.exists():
        shutil.copy2(image_path, img_dest)

    # ── LUT ──
    lut_paths = apply_lut(image_path, lut_dir, luts=["fire", "royal", "viridis"])

    # ── ELA ──
    ela_result = compute_ela(image_path)
    if "error" not in ela_result:
        ela_path = ela_dir / f"{stem}_ela.png"
        if not ela_path.exists():
            cv2.imwrite(str(ela_path), ela_result["ela_image"])
    ela_evidence = (
        [f"均值差={ela_result.get('mean_diff', 0):.2f}, 标准差={ela_result.get('std_diff', 0):.2f}"]
        if "error" not in ela_result
        else [ela_result["error"]]
    )

    # ── Clone ──
    clone_result = detect_clones(image_path)
    if "error" not in clone_result:
        n = clone_result.get("clone_count", 0)
        if n > 0:
            clone_path = clone_dir / f"{stem}_clone.png"
            if not clone_path.exists():
                draw_clone_boxes(
                    image_path, clone_result["clones"],
                    clone_path, clone_result.get("block_size", 32),
                )
    clone_evidence = (
        [f"发现 {clone_result.get('clone_count', 0)} 对相似块"]
        if "error" not in clone_result
        else [clone_result["error"]]
    )

    # ── Build CheckResults ──
    results: list[CheckResult] = [
        CheckResult(
            check_id="images.lut",
            check_name="LUT 伪彩映射",
            level="green",
            verdict=f"已对图片应用 LUT 伪彩映射，输出 {len(lut_paths)} 张对比图",
            evidence=[f"输出: {p.name}" for p in lut_paths],
            confidence=0.5,
            needs_human=True,
            human_instruction="拼接区域在伪彩色下会出现不自然的颜色断层。注意区分 JPEG 8×8 压缩伪影方块。",
        ),
        CheckResult(
            check_id="images.ela",
            check_name="误差水平分析 (ELA)",
            level="green",
            verdict="已生成 ELA 热力图供人工审查",
            evidence=ela_evidence,
            confidence=0.4,
            needs_human=True,
            human_instruction="高亮区域表示压缩误差与周围不同，可能来自不同来源。",
        ),
        CheckResult(
            check_id="images.clone_detect",
            check_name="克隆区域检测",
            level="green",
            verdict="已检测图片并保存 Clone 检测图供人工审查",
            evidence=clone_evidence,
            confidence=0.7,
            needs_human=True,
            human_instruction="红框连线标记了哈希一致的图像块对，需人工确认。",
        ),
    ]

    # ── Aggregate + format ──
    aggregated = aggregate_results(results)
    paper = SimpleNamespace(
        title=f"单图取证: {image_path.name}",
        journal="",
        year=None,
        authors=[],
    )

    image_artifacts: dict[str, list[str]] = {
        "lut_output": sorted(
            str(p.relative_to(output_dir)) for p in lut_dir.glob("*.png")
        ),
        "ela_output": sorted(
            str(p.relative_to(output_dir)) for p in ela_dir.glob("*.png")
        ),
        "clone_output": sorted(
            str(p.relative_to(output_dir)) for p in clone_dir.glob("*.png")
        ),
    }

    json_str = format_json(aggregated, results, paper=paper, image_artifacts=image_artifacts)
    report_path = output_dir / "report.json"
    report_path.write_text(json_str, encoding="utf-8")
    return report_path


# ── Landing page (no report loaded) ──────────────────────────────────────────
if _report_file is None:
    st.title("🔬 Paper Fraud Dashboard")

    if _available_reports:
        # Multi-report mode: show list of available reports
        st.markdown("### 已检测的论文")
        st.markdown(f"共 {len(_available_reports)} 篇报告，点击可打开：")
        for r in _available_reports:
            level_emoji = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢", "error": "⚪"}
            emoji = level_emoji.get(r["overall_level"], "⚪")
            score = r.get("risk_score", 0)
            score_str = f" — 风险评分 {score:.0f}/100" if score > 0 else ""
            meta = []
            if r.get("journal"):
                meta.append(r["journal"])
            if r.get("year"):
                meta.append(str(r["year"]))
            meta_str = f" ({', '.join(meta)})" if meta else ""

            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"{emoji} **{r['title'][:120]}**{meta_str}{score_str}")
            with col2:
                if st.button("打开", key=f"open_{r['path']}"):
                    st.query_params["report"] = r["path"]
                    st.rerun()
            st.divider()

    st.markdown("### 或手动打开一份检测报告")
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

    st.caption("上传 JSON 仅能查看文本报告，图像取证需粘贴路径（图片目录不在上传文件中）。")

    if st.button("打开报告", type="primary") and path_input:
        resolved = _resolve(Path(path_input.strip()))
        if resolved:
            st.query_params["report"] = str(resolved)
            st.rerun()
        else:
            st.error(f"文件不存在: {path_input.strip()}")

    if uploaded is not None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.write(uploaded.read())
        tmp.close()
        st.query_params["report"] = str(Path(tmp.name))
        st.rerun()

    # ── Image upload ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**或上传单张图片进行图像取证**")
    st.caption("上传后自动运行 LUT 伪彩映射 + ELA 误差分析 + 克隆区域检测")

    uploaded_image = st.file_uploader(
        "上传图片",
        type=["png", "jpg", "jpeg", "tiff", "bmp", "webp"],
        label_visibility="collapsed",
    )

    if uploaded_image is not None:
        content = uploaded_image.read()
        img_hash = hashlib.md5(content).hexdigest()[:8]
        output_dir = Path("output") / f"_img_{img_hash}"
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(uploaded_image.name).suffix or ".png"
        img_path = images_dir / f"input{suffix}"
        if not img_path.exists():
            img_path.write_bytes(content)

        report_path = output_dir / "report.json"
        if not report_path.exists():
            with st.spinner("正在执行图像取证分析 (LUT 伪彩 / ELA 热力 / 克隆检测)..."):
                report_path = _generate_image_report(img_path, output_dir)

        st.query_params["report"] = str(report_path)
        st.rerun()

    if not _available_reports:
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

from paperfraud.web.image_utils import (
    get_output_subdirs,
    load_image_for_web,
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

    'page4_1' with Figure 1  → '第 4 页（Figure 1）'
    'page5_1' without caption → '第 5 页'
    'page5_1', 'page5_2' (multiple) → '第 5 页 · 图 1', '第 5 页 · 图 2'
    """
    m = re.match(r"page(\d+)(?:_(\d+))?$", name)
    if m:
        page = m.group(1)
        idx = m.group(2)
        multi = False
        if stems:
            prefix = f"page{page}_"
            multi = sum(1 for s in stems if s.startswith(prefix)) > 1
        fig_info = figure_captions.get(page, {})
        fig_num = fig_info.get("figure", "")
        fig_suffix = f"（Figure {fig_num}）" if fig_num else ""
        if multi and idx:
            return f"第 {page} 页 · 图 {idx}{fig_suffix}"
        return f"第 {page} 页{fig_suffix}"
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


def _encode_data_url(path: Path, max_width: int | None = None, *, prefer_jpeg: bool = False) -> str:
    """Convert an image file to a base64 data URL, optionally downsizing.

    Args:
        path: Image file path.
        max_width: If set and image is wider, resize to this width.
        prefer_jpeg: If True and the image is RGB, use JPEG quality=92
            instead of PNG. JPEG reduces size by ~70% vs PNG for photos,
            critical for avoiding 10MB+ HTML over WebSocket.

            Non-RGB images (RGBA, P, grayscale) always use PNG to preserve
            transparency/color mode fidelity.
    """
    import base64
    from io import BytesIO

    from PIL import Image

    img = Image.open(path)
    if max_width and img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

    buf = BytesIO()
    if prefer_jpeg and img.mode == "RGB":
        img.save(buf, format="JPEG", quality=92)
        mime = "image/jpeg"
    else:
        img.save(buf, format="PNG")
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def _render_zoom_iframe(ref_path: Path, images: list[tuple[str, Path]], key: str, *, grid_cols: int = 2) -> None:
    """Render a self-contained iframe with Canvas selector + zoomed crop panels.

    All image rendering (reference thumbnail + zoomed crops) happens inside
    the iframe via Canvas 2D.  No JS→Python communication is needed — the
    iframe is entirely self-contained.

    Args:
        ref_path: Path to the reference image (resized to 300px wide as PNG).
        images: List of (label, path) tuples for the zoom panels.
        key: Unique key for this instance (e.g. "lut", "ela", "clone").
        grid_cols: Number of columns in the right-side zoom grid.
    """
    import base64
    from io import BytesIO

    from PIL import Image

    # ── Reference thumbnail (same max as analysis, PNG) ──────────────────
    ref_data_url = _encode_data_url(ref_path, max_width=2000, prefer_jpeg=False)
    ref_img = Image.open(ref_path)
    ref_w = min(408, ref_img.width)
    ref_h = int(ref_img.height * (ref_w / ref_img.width))

    # ── Analysis images (max 2000px, JPEG Q92 for forensic detail) ───────
    img_entries: list[dict] = []
    for label, img_path in images:
        if img_path.exists():
            img_entries.append({
                "label": label,
                "filename": img_path.name,
                "dataUrl": _encode_data_url(img_path, max_width=2000, prefer_jpeg=True),
            })

    images_json = json.dumps(img_entries)
    ref_json = json.dumps(ref_data_url)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:#0e1117; color:#fafafa; font-family:system-ui,sans-serif; user-select:none; }}
  #s {{ font-size:11px; color:#aaa; padding:4px 8px; background:#1a1a2e; border-bottom:1px solid #333; }}
  .main {{ display:flex; gap:0; }}
  .left {{ flex:0 0 420px; padding:6px; }}
  .left canvas {{ display:block; max-width:100%; cursor:crosshair; border-radius:4px; }}
  .right {{ flex:1; min-width:0; padding:6px; display:flex; flex-direction:column; gap:6px; }}
  .grid {{ display:grid; gap:6px; }}
  .panel {{ background:#1a1a2e; border-radius:6px; overflow:hidden; }}
  .panel-label {{ font-size:12px; color:#aaa; padding:4px 8px; background:#222; }}
  .panel canvas {{ display:block; width:100%; height:auto; image-rendering:pixelated; }}
</style></head><body>
<div id="s">拖拽画框 · 拖动已选框 · 双击清除</div>
<div class="main">
  <div class="left"><canvas id="rc"></canvas></div>
  <div class="right"><div class="grid" id="grid"></div></div>
</div>
<script>
(function(){{
var IMAGES = {images_json};
var REF_URL = {ref_json};
var GRID_COLS = {json.dumps(grid_cols)};
var KEY = {json.dumps(key)};

var refCanvas = document.getElementById('rc');
var refCtx = refCanvas.getContext('2d');
var statusEl = document.getElementById('s');
var gridEl = document.getElementById('grid');
var refImg = null;
var zoomImgs = [];      // {{img, canvas, label}}
var rect = null;        // px rect on reference canvas
var currentCropPct = null;  // stored so onload handlers can re-apply after image loads
var drawing = false, dragging = false;
var startX = 0, startY = 0, dragOffX = 0, dragOffY = 0;
var MIN = 6;

// ── setFrameHeight ─────────────────────────────────────────────
function setHeight() {{
  var h = document.body.scrollHeight;
  window.parent.postMessage({{
    isStreamlitMessage: true,
    type: 'streamlit:setFrameHeight',
    height: h
  }}, '*');
}}

// ── Reference canvas drawing ────────────────────────────────────
function drawRef() {{
  if (!refImg) return;
  refCtx.clearRect(0, 0, refCanvas.width, refCanvas.height);
  refCtx.drawImage(refImg, 0, 0);
  if (!rect || rect.w < MIN || rect.h < MIN) return;
  refCtx.strokeStyle = '#ff3333'; refCtx.lineWidth = 2;
  refCtx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  refCtx.fillStyle = 'rgba(255,50,50,0.12)';
  refCtx.fillRect(rect.x, rect.y, rect.w, rect.h);
}}

function inside(mx, my) {{
  if (!rect) return false;
  return mx >= rect.x - 6 && mx <= rect.x + rect.w + 6 &&
         my >= rect.y - 6 && my <= rect.y + rect.h + 6;
}}

// ── Render a single zoom panel ──────────────────────────────────
// Called both from updateAllZooms AND from img.onload (which may fire
// after the user already drew a box — race condition fix).
function renderZoomToCanvas(imgObj, canvas, cropPct) {{
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var cw = canvas.offsetWidth * dpr;
  var ch = canvas.offsetHeight * dpr;
  if (cw < 4 || ch < 4) return;
  canvas.width = cw;
  canvas.height = ch;

  if (cropPct && imgObj.naturalWidth && imgObj.naturalHeight) {{
    ctx.imageSmoothingEnabled = false;
    var iw = imgObj.naturalWidth, ih = imgObj.naturalHeight;
    var sx = cropPct.x / 100 * iw;
    var sy = cropPct.y / 100 * ih;
    var sw = cropPct.w / 100 * iw;
    var sh = cropPct.h / 100 * ih;
    ctx.drawImage(imgObj, sx, sy, sw, sh, 0, 0, cw, ch);
    // Match panel aspect ratio to crop region
    canvas.style.aspectRatio = (cropPct.w / cropPct.h).toFixed(4);
  }} else {{
    // No crop or image not yet loaded → show full image
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(imgObj, 0, 0, cw, ch);
    // Use image's natural aspect ratio
    if (imgObj.naturalWidth && imgObj.naturalHeight) {{
      canvas.style.aspectRatio = (imgObj.naturalWidth / imgObj.naturalHeight).toFixed(4);
    }}
  }}
}}

function updateAllZooms() {{
  if (!rect || rect.w < MIN || rect.h < MIN) {{
    // Clear selection → show full images
    currentCropPct = null;
    for (var i = 0; i < zoomImgs.length; i++) {{
      var zi = zoomImgs[i];
      renderZoomToCanvas(zi.img, zi.canvas, null);
    }}
    statusEl.textContent = '拖拽画框 · 拖动已选框 · 双击清除';
    return;
  }}
  currentCropPct = {{
    x: rect.x / refCanvas.width * 100,
    y: rect.y / refCanvas.height * 100,
    w: rect.w / refCanvas.width * 100,
    h: rect.h / refCanvas.height * 100
  }};
  for (var i = 0; i < zoomImgs.length; i++) {{
    var zi = zoomImgs[i];
    renderZoomToCanvas(zi.img, zi.canvas, currentCropPct);
  }}
  statusEl.textContent = '选中: ' + currentCropPct.x.toFixed(1) + '%, ' + currentCropPct.y.toFixed(1) +
    '%, ' + currentCropPct.w.toFixed(1) + '% × ' + currentCropPct.h.toFixed(1) + '%';
}}

// ── Build zoom panels ───────────────────────────────────────────
function buildZoomPanels() {{
  gridEl.style.gridTemplateColumns = 'repeat(' + GRID_COLS + ', 1fr)';
  gridEl.innerHTML = '';
  zoomImgs = [];
  for (var i = 0; i < IMAGES.length; i++) {{
    var entry = IMAGES[i];
    var panel = document.createElement('div');
    panel.className = 'panel';
    var label = document.createElement('div');
    label.className = 'panel-label';
    label.textContent = entry.label;
    var cv = document.createElement('canvas');
    cv.style.display = 'block';
    cv.style.width = '100%';
    // Aspect ratio set dynamically in renderZoomToCanvas (from image or crop)
    panel.appendChild(label);
    panel.appendChild(cv);
    gridEl.appendChild(panel);

    var img = new Image();
    img.onload = function(canvas, imgObj) {{
      return function() {{
        // renderZoomToCanvas sets aspectRatio from image dimensions (no crop)
        // or crop rectangle.  Handles race: if user drew a box before image
        // loaded, currentCropPct will be non-null.
        requestAnimationFrame(function() {{
          renderZoomToCanvas(imgObj, canvas, currentCropPct);
        }});
      }};
    }}(cv, img);
    img.src = entry.dataUrl;
    zoomImgs.push({{img: img, canvas: cv, label: entry.label}});
  }}
  // Re-measure height after panels are built
  requestAnimationFrame(function() {{ setHeight(); }});
}}

// ── Mouse events on reference canvas ────────────────────────────
// Scale CSS-offset mouse coords to canvas-bitmap coords (needed when
// canvas bitmap resolution differs from CSS display size, e.g. retina).
function toBitmap(mx, my) {{
  return {{
    x: mx * refCanvas.width / refCanvas.offsetWidth,
    y: my * refCanvas.height / refCanvas.offsetHeight
  }};
}}

refCanvas.addEventListener('mousedown', function(e) {{
  var pt = toBitmap(e.offsetX, e.offsetY);
  var mx = pt.x, my = pt.y;
  if (inside(mx, my)) {{
    dragging = true;
    dragOffX = mx - rect.x;
    dragOffY = my - rect.y;
  }} else {{
    drawing = true;
    rect = {{x: mx, y: my, w: 0, h: 0}};
    startX = mx; startY = my;
  }}
  e.preventDefault();
}});

refCanvas.addEventListener('mousemove', function(e) {{
  var pt = toBitmap(e.offsetX, e.offsetY);
  var mx = pt.x, my = pt.y;
  if (drawing) {{
    rect.x = Math.min(startX, mx);
    rect.y = Math.min(startY, my);
    rect.w = Math.abs(mx - startX);
    rect.h = Math.abs(my - startY);
    drawRef();
  }} else if (dragging && rect) {{
    rect.x = Math.max(0, Math.min(mx - dragOffX, refCanvas.width - rect.w));
    rect.y = Math.max(0, Math.min(my - dragOffY, refCanvas.height - rect.h));
    drawRef();
  }}
  refCanvas.style.cursor = inside(mx, my) ? 'move' : 'crosshair';
}});

refCanvas.addEventListener('mouseup', function() {{
  var didSomething = drawing || dragging;
  drawing = false; dragging = false;
  if (didSomething && rect && rect.w >= MIN && rect.h >= MIN) {{
    updateAllZooms();
  }}
}});

refCanvas.addEventListener('dblclick', function() {{
  rect = null;
  drawRef();
  updateAllZooms();
  statusEl.textContent = '已清除 · 重新拖拽画框';
}});

refCanvas.addEventListener('mouseleave', function() {{
  if (drawing || dragging) {{
    refCanvas.dispatchEvent(new MouseEvent('mouseup'));
  }}
}});

// ── Load reference image ────────────────────────────────────────
refImg = new Image();
refImg.onload = function() {{
  refCanvas.width = refImg.naturalWidth;
  refCanvas.height = refImg.naturalHeight;
  drawRef();
  buildZoomPanels();
  setHeight();
}};
refImg.src = REF_URL;
}})();
</script></body></html>"""

    # Estimate iframe height: ref image + zoom grid + status bar
    # Zoom: for 4 images in 2 cols → 2 rows, each canvas ~(container/2)*3/4 tall
    # Rough estimate: ref_h + rows * 280 + 40
    n_rows = (len(img_entries) + grid_cols - 1) // grid_cols
    est_height = ref_h + n_rows * 300 + 60
    st.components.v1.html(html, height=est_height, scrolling=True)


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
    ref_path = original if original else (fire_p if fire_p.exists() else (royal_p if royal_p.exists() else viridis_p))

    # Build image list for the all-in-iframe widget
    images: list[tuple[str, Path]] = []
    if original:
        images.append(("原始图片", original))
    else:
        images.append(("参考图", ref_path))
    if fire_p.exists():
        images.append(("Fire/Iron", fire_p))
    if royal_p.exists():
        images.append(("Royal", royal_p))
    if viridis_p.exists():
        images.append(("Viridis", viridis_p))

    _render_zoom_iframe(ref_path, images, "lut", grid_cols=2)

    # Download buttons (Streamlit-native, outside iframe)
    st.markdown("---")
    st.caption("📥 下载原始文件")
    dl_cols = st.columns(len(images))
    for i, (label, path) in enumerate(images):
        with dl_cols[i]:
            with open(path, "rb") as f:
                st.download_button(label, f.read(), path.name, key=f"dl_lut_{selected}_{i}")


def _clone_viewer(clone_dir: Path) -> None:
    files = sorted(clone_dir.glob("*_clone.png"), key=lambda f: _natural_key(f.name))
    if not files:
        st.info("未发现克隆标记图。")
        return

    filenames = [f.name for f in files]
    stems = [f.replace("_clone.png", "") for f in filenames]

    selected = st.selectbox(
        "选择克隆标记图",
        filenames,
        format_func=lambda x: _image_label(x.replace("_clone.png", ""), stems),
    )
    if not selected:
        return

    path = clone_dir / selected
    original = _find_original(selected, "clone")
    ref_path = original if original else path

    images: list[tuple[str, Path]] = []
    if original:
        images.append(("原始图片", original))
    images.append(("克隆标记", path))

    _render_zoom_iframe(ref_path, images, "clone", grid_cols=2)

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
    if not selected:
        return

    path = ela_dir / selected
    original = _find_original(selected, "ela")
    ref_path = original if original else path

    images: list[tuple[str, Path]] = []
    if original:
        images.append(("原始图片", original))
    images.append(("ELA 热力图", path))

    _render_zoom_iframe(ref_path, images, "ela", grid_cols=2)

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

    # Collect all images (png, jpg, jpeg)
    all_images: list[Path] = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        all_images.extend(sorted(images_dir.glob(ext), key=lambda p: _natural_key(p.name)))
    stems = sorted({p.stem for p in all_images}, key=_natural_key)

    # ── Single-image mode: upload comparison image ─────────────────────────
    if len(stems) < 2:
        st.info("当前仅有 1 张图片。上传第二张图片进行 Sync/Blink/Diff 比对。")

        if not stems:
            st.warning("未找到任何图片。")
            return

        # Image A: the only available image
        stem_a = stems[0]
        img_a_path = next(
            (images_dir / f"{stem_a}{ext}" for ext in (".png", ".jpg", ".jpeg")
             if (images_dir / f"{stem_a}{ext}").exists()),
            None,
        )
        if img_a_path is None:
            st.error("无法找到图片文件。")
            return

        st.caption(f"**图片 A（原图）：** {img_a_path.name}")

        # Image B: upload
        uploaded_b = st.file_uploader(
            "上传比对图片（图片 B）",
            type=["png", "jpg", "jpeg", "tiff", "bmp", "webp"],
            key="cmp_upload_b",
        )

        if uploaded_b is None:
            return

        # Save uploaded comparison image to a temp file
        cmp_dir = report_dir / "_comparison"
        cmp_dir.mkdir(exist_ok=True)
        suffix = Path(uploaded_b.name).suffix or ".png"
        img_b_path = cmp_dir / f"comparison{suffix}"
        img_b_path.write_bytes(uploaded_b.read())

        st.success(f"已加载比对图片: {uploaded_b.name}")
        st.markdown("---")
        st.caption(
            "**Sync 同步放大镜** — 滚轮缩放、拖拽平移，两图同步。"
            " **Blink 闪烁对比** — 原位交替闪烁，拼接边缘最易察觉。"
            " **Diff 差异混合** — 像素级减法热力图。"
            " **LUT 伪彩映射** — 伪彩色放大灰度断层。"
        )
        _comparison_component(img_a_path, img_b_path)
        return

    # ── Multi-image mode: select two images ─────────────────────────────────
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
        # Resolve paths (handle different extensions)
        img_a_path = None
        img_b_path = None
        for ext in (".png", ".jpg", ".jpeg"):
            candidate = images_dir / f"{img_a_stem}{ext}"
            if candidate.exists() and img_a_path is None:
                img_a_path = candidate
            candidate = images_dir / f"{img_b_stem}{ext}"
            if candidate.exists() and img_b_path is None:
                img_b_path = candidate
        if img_a_path is None or img_b_path is None:
            st.error("无法找到图片文件。")
            return

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


# ── Interactive Chat ─────────────────────────────────────────────────────────

def _interactive_chat(report: dict, summary: dict, checks: list) -> None:
    """LLM-powered Q&A page. Report JSON is injected as system context;
    conversation history stays clean (no JSON duplication)."""
    import os

    from paperfraud.review.prompts import INTERACTIVE_CHAT_PROMPT

    st.subheader("💬 交互问答")
    st.caption("基于当前检测报告追问细节——为什么某个信号触发？证据链是否可靠？")

    # ── Check API key ────────────────────────────────────────────────────
    api_key = os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        st.warning("未设置 API Key。请设置 `DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY` 环境变量。")
        return

    provider = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else (
        "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
    )

    # ── Build system context (report JSON, once) ──────────────────────────
    report_context = json.dumps({
        "summary": summary,
        "checks": [
            {
                "check_id": c.get("check_id", ""),
                "check_name": c.get("check_name", ""),
                "level": c.get("level", ""),
                "verdict": c.get("verdict", ""),
                "evidence": c.get("evidence", [])[:3],
                "confidence": c.get("confidence", 1.0),
            }
            for c in checks
        ],
        "risk_breakdown": summary.get("risk_breakdown", {}),
    }, indent=2, ensure_ascii=False)

    system_msg = INTERACTIVE_CHAT_PROMPT + "\n\n" + report_context

    # ── Init session messages (no report data in history) ─────────────────
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # ── Render history ────────────────────────────────────────────────────
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Chat input ────────────────────────────────────────────────────────
    if prompt := st.chat_input("输入你的问题，比如：为什么 method_misuse 触发了？"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("思考中..."):
                response_text = _call_chat_llm(system_msg, st.session_state.chat_messages, provider)
            st.markdown(response_text)

        st.session_state.chat_messages.append({"role": "assistant", "content": response_text})

    # ── Clear button ──────────────────────────────────────────────────────
    if st.session_state.chat_messages and st.button("清空对话"):
        st.session_state.chat_messages = []
        st.rerun()


def _call_chat_llm(system_msg: str, messages: list[dict], provider: str) -> str:
    """Call LLM for interactive chat. System message carries report context;
    messages carry only the conversation history."""
    import os

    if provider == "deepseek":
        from openai import OpenAI
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
            ] + [
                {"role": m["role"], "content": m["content"]}
                for m in messages
            ],
            temperature=0,
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""

    elif provider == "anthropic":
        from anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        client = Anthropic(api_key=api_key)
        # Anthropic doesn't have system-message-only; merge into user content
        msg = client.messages.create(
            model=model,
            system=system_msg,
            messages=[
                {"role": m["role"], "content": m["content"]}
                for m in messages
            ],
            temperature=0,
            max_tokens=2048,
        )
        return msg.content[0].text if msg.content else ""

    else:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
            ] + [
                {"role": m["role"], "content": m["content"]}
                for m in messages
            ],
            temperature=0,
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🔬 Paper Fraud Detection")

# ── Multi-report selector ─────────────────────────────────────────────────
if _reports_dir is not None and _available_reports:
    # Build display labels
    report_options = {}
    for r in _available_reports:
        score = r.get("risk_score", 0)
        label = f"{r['title'][:80]} ({score:.0f}分)" if score > 0 else r['title'][:80]
        report_options[label] = r["path"]

    current_path = str(_report_file) if _report_file else ""
    current_label = None
    for label, path in report_options.items():
        if path == current_path:
            current_label = label
            break

    selected_label = st.sidebar.selectbox(
        "📄 切换报告",
        list(report_options.keys()),
        index=list(report_options.keys()).index(current_label) if current_label else 0,
        key="report_selector",
    )

    # On selection change, update URL and rerun
    if report_options[selected_label] != current_path:
        st.query_params["report"] = report_options[selected_label]
        st.rerun()

    st.sidebar.markdown("---")

if st.sidebar.button("🏠 返回首页", use_container_width=True):
    st.query_params.clear()
    st.rerun()

page = st.sidebar.radio("导航", [
    "📊 检测总览",
    "🔴 Red 信号",
    "🟠 Orange 信号",
    "🟡 Yellow 信号",
    "🖼️ 图像取证",
    "🔍 人工审查工作台",
    "💬 交互问答",
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

elif page == "💬 交互问答":
    _interactive_chat(report, summary, checks)

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
