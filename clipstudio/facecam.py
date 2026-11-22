"""Find the streamer's face cam: a small face that stays in one spot near an edge.

Ported from gta-face-clipper (choose_facecam_roi / detect_smart_frame). Faces
that move around (NPCs, cutscenes) don't count. If unsure we return None and
the buyer can drag a box over the cam by hand.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"


def choose_facecam_roi(frame_width, frame_height, face_samples, target_aspect=1080 / 760):
    """Pixel box (x, y, w, h) of a persistent edge face cam, or None."""
    if frame_width <= 0 or frame_height <= 0 or not face_samples:
        return None
    threshold = max(3, math.ceil(len(face_samples) * 0.3))
    candidates = [
        box
        for detections in face_samples
        for box in detections
        if len(box) >= 4
        and 0 < box[2] < frame_width * 0.12
        and 0 < box[3] < frame_height * 0.22
        and (
            box[0] + box[2] / 2 < frame_width * 0.38
            or box[0] + box[2] / 2 > frame_width * 0.62
        )
    ]
    if len(candidates) < threshold:
        return None
    cx = np.asarray([b[0] + b[2] / 2 for b in candidates], dtype=float)
    cy = np.asarray([b[1] + b[3] / 2 for b in candidates], dtype=float)
    mx, my = float(np.median(cx)), float(np.median(cy))
    near = [
        b
        for b in candidates
        if abs((b[0] + b[2] / 2) - mx) <= frame_width * 0.035
        and abs((b[1] + b[3] / 2) - my) <= frame_height * 0.05
    ]
    if len(near) < threshold:
        return None
    if (
        frame_width * 0.25 <= mx <= frame_width * 0.75
        and frame_height * 0.25 <= my <= frame_height * 0.75
    ):
        return None
    face_w = float(np.median([b[2] for b in near]))
    face_h = float(np.median([b[3] for b in near]))
    height = min(frame_height, max(face_h * 2.8, face_w * target_aspect * 1.15))
    width = min(frame_width, height * target_aspect * 1.15)
    height = width / target_aspect
    center_y = my + face_h * 0.15
    x = int(round(min(max(0, mx - width / 2), frame_width - width)))
    y = int(round(min(max(0, center_y - height / 2), frame_height - height)))
    w, h = int(round(width)), int(round(height))
    if w < frame_width * 0.12 or h < frame_height * 0.1:
        return None
    return x, y, w, h


def _median_face(frame_width, frame_height, face_samples):
    """The persistent edge face (median box) the cam estimate was built from."""
    boxes = [
        b
        for detections in face_samples
        for b in detections
        if 0 < b[2] < frame_width * 0.12
        and 0 < b[3] < frame_height * 0.22
        and (b[0] + b[2] / 2 < frame_width * 0.38 or b[0] + b[2] / 2 > frame_width * 0.62)
    ]
    if not boxes:
        return None
    mx = float(np.median([b[0] + b[2] / 2 for b in boxes]))
    my = float(np.median([b[1] + b[3] / 2 for b in boxes]))
    near = [
        b
        for b in boxes
        if abs(b[0] + b[2] / 2 - mx) <= frame_width * 0.035
        and abs(b[1] + b[3] / 2 - my) <= frame_height * 0.05
    ]
    if not near:
        return None
    fw = float(np.median([b[2] for b in near]))
    fh = float(np.median([b[3] for b in near]))
    return mx - fw / 2, my - fh / 2, fw, fh


def _peaks(scores, positions):
    positions = [p for p in positions if 0 <= p < len(scores)]
    if not positions:
        return []
    values = np.asarray([scores[p] for p in positions])
    threshold = max(0.2, 4 * float(np.median(values)))
    peaks = [p for p in positions if scores[p] >= threshold]
    # one position per edge line (edges can be 2 px wide)
    merged = []
    for p in peaks:
        if not merged or abs(p - merged[-1]) > 3:
            merged.append(p)
    return merged


def refine_to_panel(edges, face, frame_width, frame_height, aspect=16 / 9):
    """Snap to the cam overlay's border: straight edges that stay put while gameplay
    changes. Webcams are about 16:9, so of the candidate borders we keep the pair
    whose shape is closest to that (a static door frame behind the streamer is a
    persistent edge too). `edges` = mean of per-frame edge maps (0..1)."""
    fx, fy, fw, fh = face
    reach_x, reach_y = 5 * fw, 4 * fh
    c0, c1 = int(max(0, fx - 0.5 * fw)), int(min(frame_width, fx + 1.5 * fw))
    row_scores = edges[:, c0:c1].mean(axis=1)
    tops = _peaks(row_scores, range(int(fy) - 2, int(fy - reach_y) - 1, -1))
    top = tops[0] if tops else (0 if fy - reach_y <= 0 else fy - 1.2 * fh)
    bottoms = _peaks(row_scores, range(int(fy + fh) + 2, int(fy + fh + reach_y)))
    bottom = bottoms[0] if bottoms else (
        frame_height if fy + fh + reach_y >= frame_height else fy + fh + 1.5 * fh
    )
    r0, r1 = int(max(0, top + 3)), int(min(frame_height, bottom - 3))
    if r1 <= r0:
        return None
    column_scores = edges[r0:r1, :].mean(axis=0)
    lefts = _peaks(column_scores, range(int(fx) - 2, int(fx - reach_x) - 1, -1))
    rights = _peaks(column_scores, range(int(fx + fw) + 2, int(fx + fw + reach_x)))
    if fx - reach_x <= 0:
        lefts.append(0)
    if fx + fw + reach_x >= frame_width:
        rights.append(frame_width)
    height = float(bottom - top)
    if not lefts or not rights or height <= 0:
        return None
    left, right = min(
        ((l, r) for l in lefts for r in rights if r - l >= 1.4 * fw),
        key=lambda pair: abs((pair[1] - pair[0]) / height - aspect),
        default=(None, None),
    )
    if left is None or not (1.1 <= (right - left) / height <= 2.6):
        return None
    left, top = max(0.0, float(left)), max(0.0, float(top))
    right, bottom = min(float(frame_width), float(right)), min(float(frame_height), float(bottom))
    width, height = right - left, bottom - top
    if width < 1.4 * fw or height < 1.2 * fh or width > frame_width * 0.6 or height > frame_height * 0.7:
        return None
    # Step inside the border: cam frames, follower counters and name tags sit right on the edge.
    inset_y, inset_x = max(3.0, 0.045 * height), max(3.0, 0.02 * width)
    left, right = left + inset_x, right - inset_x
    top, bottom = top + inset_y, bottom - inset_y
    return int(left), int(top), int(right - left), int(bottom - top)


def _detect_faces(detector, frame, frame_width, frame_height):
    import cv2

    scale = min(320 / frame_width, 320 / frame_height)
    resized = cv2.resize(
        frame,
        (round(frame_width * scale), round(frame_height * scale)),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.zeros((320, 320, 3), dtype=np.uint8)
    pad_x = (320 - resized.shape[1]) // 2
    pad_y = (320 - resized.shape[0]) // 2
    canvas[pad_y : pad_y + resized.shape[0], pad_x : pad_x + resized.shape[1]] = resized
    _, faces = detector.detect(canvas)
    boxes = []
    factor = 1.0 / scale
    for face in faces if faces is not None else []:
        x, y, w, h = (int(v) for v in face[:4])
        bx, by = int((x - pad_x) * factor), int((y - pad_y) * factor)
        bw, bh = int(w * factor), int(h * factor)
        bx0, by0 = max(0, bx), max(0, by)
        bw, bh = min(frame_width, bx + bw) - bx0, min(frame_height, by + bh) - by0
        if bw > 0 and bh > 0:
            boxes.append((bx0, by0, bw, bh))
    return boxes


def _detect_faces_tiled(detector, frame, frame_width, frame_height):
    """Whole frame plus the four corners at higher zoom: cams sit in corners and
    their faces are often too small for the detector at full-frame scale."""
    boxes = list(_detect_faces(detector, frame, frame_width, frame_height))
    tw, th = int(frame_width * 0.42), int(frame_height * 0.6)
    for ox in (0, frame_width - tw):
        for oy in (0, frame_height - th):
            tile = frame[oy : oy + th, ox : ox + tw]
            for x, y, w, h in _detect_faces(detector, tile, tw, th):
                box = (x + ox, y + oy, w, h)
                if not any(abs(box[0] - b[0]) < w * 0.5 and abs(box[1] - b[1]) < h * 0.5 for b in boxes):
                    boxes.append(box)
    return boxes


def detect_facecam(video_path, start=0.0, length=None, samples=12):
    """Normalised [x, y, w, h] of the face cam in the source, or None."""
    try:
        import cv2
    except ImportError:
        return None
    if not MODEL.is_file():
        return None
    try:
        detector = cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), 0.8, 0.3, 5000)
    except cv2.error:
        return None
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    try:
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        total = frames / fps if frames > 0 else 0
        if length is None:
            length = max(1.0, total - start) if total else 60.0
        if frame_width <= 0 or frame_height <= 0 or frame_width / frame_height <= 0.75:
            return None
        found, edge_maps = [], []
        for fraction in np.linspace(0.05, 0.95, samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + length * fraction) * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                found.append([])
                continue
            found.append(_detect_faces_tiled(detector, frame, frame_width, frame_height))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            edge_maps.append(cv2.Canny(gray, 60, 160) > 0)
    finally:
        cap.release()
    box = choose_facecam_roi(frame_width, frame_height, found)
    if not box:
        return None
    face = _median_face(frame_width, frame_height, found)
    if face and len(edge_maps) >= 4:
        edges = np.mean(np.stack(edge_maps), axis=0)
        box = refine_to_panel(edges, face, frame_width, frame_height) or box
    x, y, w, h = box
    return [
        round(x / frame_width, 4),
        round(y / frame_height, 4),
        round(w / frame_width, 4),
        round(h / frame_height, 4),
    ]
