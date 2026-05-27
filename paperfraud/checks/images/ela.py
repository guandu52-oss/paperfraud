"""Error Level Analysis (ELA) for image forensics.

Detects spliced/edited regions by re-saving the image as JPEG and computing
the difference between original and recompressed. Regions from different
sources (different compression histories) show distinct error levels.

Limitations:
  - Already-heavy JPEG compression masks ELA signal
  - PDF-extracted images are often JPEG — ELA may show JPEG block artifacts
  - confidence capped at 0.4; always requires human review
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

from paperfraud.base import CheckResult


def compute_ela(image_path: Path, quality: int = 90, amplify: int = 20) -> dict:
    """Compute Error Level Analysis for a single image.

    Args:
        image_path: Path to source image
        quality: JPEG save quality (higher = less compression, more subtle diff)
        amplify: Multiplier to amplify pixel differences for visualization

    Returns dict with:
        ela_image: np.ndarray (BGR) — amplified diff as heatmap on original
        mean_diff, max_diff, std_diff: statistics
        suspicious: bool — whether any region exceeds 3σ
    """
    original = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if original is None:
        return {"error": "无法读取图像"}

    # Save as JPEG to temp, then re-read
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        cv2.imwrite(tmp_path, original, [cv2.IMWRITE_JPEG_QUALITY, quality])
        recompressed = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if recompressed is None:
        return {"error": "重新压缩失败"}

    # Ensure same dimensions (JPEG may pad)
    if recompressed.shape != original.shape:
        recompressed = cv2.resize(recompressed, (original.shape[1], original.shape[0]))

    # Compute absolute difference, amplify
    diff = cv2.absdiff(original, recompressed)
    diff_amplified = np.clip(diff.astype(np.float32) * amplify, 0, 255).astype(np.uint8)

    # Create overlay: original (desaturated) + heatmap diff
    original_bgr = cv2.cvtColor(original, cv2.COLOR_GRAY2BGR)
    diff_heatmap = cv2.applyColorMap(diff_amplified, cv2.COLORMAP_JET)

    # Blend: 60% original + 40% heatmap
    overlay = cv2.addWeighted(original_bgr, 0.6, diff_heatmap, 0.4, 0)

    # Statistics
    mean_diff = float(np.mean(diff))
    max_diff = float(np.max(diff))
    std_diff = float(np.std(diff))

    # Check for local anomalies (>3σ above mean in 32×32 blocks)
    h, w = diff.shape
    block_size = 32
    block_means = []
    for y in range(0, h - block_size + 1, block_size):
        for x in range(0, w - block_size + 1, block_size):
            block = diff[y:y + block_size, x:x + block_size]
            block_means.append(float(np.mean(block)))

    if block_means:
        block_mean = np.mean(block_means)
        block_std = np.std(block_means)
        threshold = block_mean + 3 * block_std
        suspicious_regions = sum(1 for m in block_means if m > threshold)
        suspicious = suspicious_regions >= 2
    else:
        suspicious = False
        suspicious_regions = 0

    return {
        "ela_image": overlay,
        "mean_diff": mean_diff,
        "max_diff": max_diff,
        "std_diff": std_diff,
        "suspicious": suspicious,
        "suspicious_regions": suspicious_regions,
    }


def run_ela(paper) -> list[CheckResult]:
    """Run ELA on all extracted images.

    ELA is a semi-automated check — it generates heatmap images for human
    review. It does NOT auto-flag as red/orange because:
      - JPEG compression in PDFs produces block artifacts that look like edits
      - Different color spaces/channels compress differently
      - Only human review can distinguish real edits from compression noise
    """
    if not paper.image_paths:
        return [
            CheckResult(
                check_id="images.ela",
                check_name="误差水平分析 (ELA)",
                level="error",
                verdict="未从 PDF 提取到图片，无法执行 ELA",
                needs_human=False,
            )
        ]

    if paper._output_dir:
        output_dir = Path(paper._output_dir) / "ela_output"
    elif paper._tmp_dir:
        output_dir = Path(paper._tmp_dir) / "ela_output"
    else:
        output_dir = Path("paperfraud_images") / "ela_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    evidence = []

    for img_path in paper.image_paths:
        result = compute_ela(img_path)
        if "error" in result:
            evidence.append(f"[{img_path.name}] {result['error']}")
            continue

        processed += 1

        # Save ELA overlay image
        out_name = f"{img_path.stem}_ela.png"
        out_path = output_dir / out_name
        cv2.imwrite(str(out_path), result["ela_image"])

        evidence.append(
            f"[{img_path.name}] 均值差={result['mean_diff']:.2f}，"
            f"标准差={result['std_diff']:.2f} → {out_name}"
        )

    return [
        CheckResult(
            check_id="images.ela",
            check_name="误差水平分析 (ELA)",
            level="green",
            verdict=f"已处理 {processed} 张图片并保存 ELA 热力图供人工审查",
            evidence=evidence[:15],
            confidence=0.4,
            needs_human=True,
            human_instruction=f"打开 {output_dir}/ 目录，逐张查看 *_ela.png 热力图。"
            "高亮（红/黄色）区域表示压缩误差与周围不同，可能来自不同来源。"
            "注意：JPEG 8×8 块边界天然存在误差差异；PDF 渲染文本/线条区域压缩特性也不同。",
        )
    ]
