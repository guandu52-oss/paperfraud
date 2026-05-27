"""Clone detection for image forensics.

Detects copy-paste forgery within a single image by:
  1. Dividing image into overlapping blocks (dynamic size based on resolution)
  2. Computing perceptual hash (averageHash) for each block
  3. Finding blocks with identical hashes at different positions
  4. Drawing bounding boxes on a marked output image

Limitations (MVP):
  - Does NOT detect rotated, scaled, or noise-added clones
  - Upgrade path: ORB/SIFT feature matching for scale/rotation invariance
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from paperfraud.base import CheckResult


def _block_size_for_image(h: int, w: int) -> int:
    """Pick block size proportional to image dimensions.

    Smaller images → smaller blocks, larger images → larger blocks.
    """
    dim = min(h, w)
    if dim < 200:
        return 16
    elif dim < 600:
        return 32
    elif dim < 1200:
        return 48
    else:
        return 64


def _average_hash(block: np.ndarray) -> bytes:
    """Compute averageHash for an image block (8×8 → 64-bit hash).

    Reimplements cv2.img_hash.averageHash which is only in opencv-contrib.
    """
    resized = cv2.resize(block, (8, 8), interpolation=cv2.INTER_LINEAR)
    avg = resized.mean()
    bits = (resized > avg).flatten()
    # Pack 8 bits → 1 byte
    return bytes(int(''.join(str(int(b)) for b in bits[i:i + 8]), 2)
                 for i in range(0, 64, 8))


def _is_uniform_block(block: np.ndarray, variance_threshold: float = 15.0) -> bool:
    """Check if a block is a uniform background (no meaningful content)."""
    return bool(np.var(block) < variance_threshold)


def detect_clones(image_path: Path) -> dict:
    """Detect cloned (copy-pasted) regions in an image.

    Returns dict with:
      - clone_count: number of unique clone pairs found
      - clones: list of {x1, y1, x2, y2, size} for each clone pair
    """
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"clone_count": 0, "clones": [], "error": "无法读取图像"}

    h, w = img.shape
    block_size = _block_size_for_image(h, w)
    stride = block_size // 2  # 50% overlap

    if block_size >= min(h, w):
        return {"clone_count": 0, "clones": [], "note": f"图像太小 ({w}×{h})，块大小 {block_size}px 不适用"}

    # Collect block hashes and positions
    hash_positions: dict[str, list[tuple[int, int]]] = {}

    for y in range(0, h - block_size + 1, stride):
        for x in range(0, w - block_size + 1, stride):
            block = img[y:y + block_size, x:x + block_size]
            if _is_uniform_block(block):
                continue
            hash_bytes = _average_hash(block)
            key = hash_bytes.hex()
            hash_positions.setdefault(key, []).append((x, y))

    # Find blocks with identical hashes at different positions
    clones = []
    for key, positions in hash_positions.items():
        if len(positions) < 2:
            continue
        # Group positions by spatial clusters (different positions = different clusters)
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                dx = abs(positions[i][0] - positions[j][0])
                dy = abs(positions[i][1] - positions[j][1])
                # Self-match: overlapping blocks of the same region
                if dx < block_size and dy < block_size:
                    continue
                clones.append({
                    "x1": positions[i][0], "y1": positions[i][1],
                    "x2": positions[j][0], "y2": positions[j][1],
                    "size": block_size,
                })

    # Deduplicate: if clones point to the same pair of regions, keep one
    unique_clones = []
    seen_pairs: set[tuple] = set()
    for c in clones:
        pair = (min(c["x1"], c["x2"]), min(c["y1"], c["y2"]),
                max(c["x1"], c["x2"]), max(c["y1"], c["y2"]))
        if not any(abs(pair[0] - p[0]) < block_size // 2 and abs(pair[1] - p[1]) < block_size // 2
                   for p in seen_pairs):
            seen_pairs.add(pair)
            unique_clones.append(c)

    return {
        "clone_count": len(unique_clones),
        "clones": unique_clones[:50],  # cap at 50
        "block_size": block_size,
    }


def draw_clone_boxes(img_path: Path, clones: list[dict], output_path: Path, block_size: int) -> Path:
    """Draw red bounding boxes around cloned regions and save marked image."""
    img = cv2.imread(str(img_path))
    if img is None:
        return output_path

    # Convert to color if grayscale
    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    for i, c in enumerate(clones[:30]):  # cap at 30 boxes
        color = (0, 0, 255)  # Red
        thickness = max(2, block_size // 16)

        # Draw clone pair with same color
        cv2.rectangle(img, (c["x1"], c["y1"]),
                      (c["x1"] + c["size"], c["y1"] + c["size"]), color, thickness)
        cv2.rectangle(img, (c["x2"], c["y2"]),
                      (c["x2"] + c["size"], c["y2"] + c["size"]), color, thickness)

        # Draw connecting line
        cx1, cy1 = c["x1"] + c["size"] // 2, c["y1"] + c["size"] // 2
        cx2, cy2 = c["x2"] + c["size"] // 2, c["y2"] + c["size"] // 2
        cv2.line(img, (cx1, cy1), (cx2, cy2), color, 1)
        cv2.putText(img, str(i + 1), (cx1, cy1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    cv2.imwrite(str(output_path), img)
    return output_path


def run_clone_detect(paper) -> list[CheckResult]:
    """Run clone detection on all extracted images."""
    if not paper.image_paths:
        return [
            CheckResult(
                check_id="images.clone_detect",
                check_name="克隆区域检测",
                level="error",
                verdict="未从 PDF 提取到图片，无法执行克隆检测。请使用 --extract-images 参数。",
                needs_human=False,
            )
        ]

    if paper._output_dir:
        output_dir = Path(paper._output_dir) / "clone_output"
    elif paper._tmp_dir:
        output_dir = Path(paper._tmp_dir) / "clone_output"
    else:
        output_dir = Path("paperfraud_images") / "clone_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_clones = 0
    processed = 0
    evidence = []

    for img_path in paper.image_paths:
        result = detect_clones(img_path)
        if "error" in result:
            evidence.append(f"[{img_path.name}] {result['error']}")
            continue
        if "note" in result:
            continue

        processed += 1
        clone_count = result["clone_count"]
        total_clones += clone_count

        if clone_count > 0:
            block_size = result.get("block_size", 32)
            out_name = f"{img_path.stem}_clone.png"
            out_path = output_dir / out_name
            draw_clone_boxes(img_path, result["clones"], out_path, block_size)
            evidence.append(
                f"[{img_path.name}] 发现 {clone_count} 对克隆区域 → {out_name}"
            )

    if total_clones == 0:
        return [
            CheckResult(
                check_id="images.clone_detect",
                check_name="克隆区域检测",
                level="green",
                verdict=f"已检测 {processed} 张图片并保存 Clone 检测图供人工审查",
                evidence=evidence[:10],
                confidence=0.7,
                needs_human=True,
                human_instruction=f"打开 {output_dir}/ 目录，逐张查看 *_clone.png 标记图。",
            )
        ]

    return [
        CheckResult(
            check_id="images.clone_detect",
            check_name="克隆区域检测",
            level="green",
            verdict=f"已检测 {processed} 张图片并保存 Clone 检测图供人工审查（含 {total_clones} 对相似块）",
            evidence=evidence[:15],
            confidence=0.7,
            needs_human=True,
            human_instruction=f"打开 {output_dir}/ 目录，逐张查看 *_clone.png 标记图。"
            "红框连线标记了哈希完全一致的图像块对。"
            "注意：PDF 低分辨率图像可能导致正常纹理被误判，需人工确认。",
        )
    ]
