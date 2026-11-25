"""Cover image for a clip: the best-looking frame plus the clip's title in big letters.

Saved as a JPG next to the clip (same name, .jpg). It can be downloaded from the clip card and
is sent as the video's thumbnail when the clip is posted (YouTube / Instagram, where Zernio
accepts one).

Picking the frame: a dozen frames from the middle of the clip are scored for sharpness, action
(how much changed since a moment before), contrast and a visible face; very dark frames lose.
The first seconds are skipped because the clip's own title card is on screen there.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import numpy as np

from .media import find_tool

FONT_FAMILY = "Anton"
YELLOW = "0xFFD400"


def cover_path(clip: Path) -> Path:
    return Path(clip).with_suffix(".jpg")


def frame_score(gray: np.ndarray, previous: np.ndarray | None, faces: int = 0) -> float:
    """Higher = better cover. Inputs are small grayscale frames (uint8)."""
    import cv2

    g = gray.astype(np.float32)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    contrast = float(g.std())
    motion = float(np.abs(g - previous.astype(np.float32)).mean()) if previous is not None else 0.0
    bright = float(g.mean())
    score = min(sharp / 400.0, 1.0) * 0.4 + min(contrast / 64.0, 1.0) * 0.25 + min(motion / 25.0, 1.0) * 0.35
    if faces:
        score += 0.3
    if bright < 35 or bright > 235:
        score -= 0.5
    return score


def _face_detector(width: int, height: int):
    try:
        import cv2

        from .facecam import MODEL

        if not MODEL.is_file():
            return None
        return cv2.FaceDetectorYN.create(str(MODEL), "", (width, height), 0.8, 0.3, 50)
    except Exception:  # noqa: BLE001 - faces are a bonus, not required
        return None


def best_frame_time(video: Path, samples: int = 12) -> float:
    """Seconds into the clip of the best cover frame."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return 1.0
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = frames / fps if frames > 0 else 10.0
        start = min(max(3.5, duration * 0.2), duration * 0.5)  # past the title card
        end = duration * 0.92
        best_t, best = max(0.0, duration * 0.5), -1e9
        detector = None
        for t in np.linspace(start, end, samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t - 0.25) * 1000)
            ok_a, before = cap.read()
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            small = cv2.resize(frame, (180, round(180 * frame.shape[0] / frame.shape[1])), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            prev = None
            if ok_a and before is not None:
                prev = cv2.cvtColor(cv2.resize(before, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA),
                                    cv2.COLOR_BGR2GRAY)
            faces = 0
            if detector is None:
                detector = _face_detector(small.shape[1] * 2, small.shape[0] * 2) or False
            if detector:
                try:
                    _, found = detector.detect(cv2.resize(small, (small.shape[1] * 2, small.shape[0] * 2)))
                    faces = 0 if found is None else len(found)
                except Exception:  # noqa: BLE001
                    faces = 0
            score = frame_score(gray, prev, faces)
            if score > best:
                best, best_t = score, float(t)
        return best_t
    finally:
        cap.release()


def wrap_title(title: str, measure, max_em: float, max_lines: int = 3) -> list[str]:
    """Greedy word wrap: each line at most `max_em` wide (measure(text) -> em). Long titles are
    cut with '…' after max_lines."""
    words = str(title or "").split()
    lines, line = [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if line and measure(trial) > max_em:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,!?") + "…"
    return lines


def _font_for(text: str) -> Path:
    from .fonts import _font, fallback_for, font_file

    font = _font(FONT_FAMILY)
    missing = next((ch for ch in text if not ch.isspace() and font and not font.has(ch)), None)
    if missing:
        path = font_file(fallback_for(missing))
        if path and path.is_file():
            return path
    return font_file(FONT_FAMILY)


def clean_still(source, at: float, design, output: Path) -> Path:
    """A 0.2 s piece of the clip's layout WITHOUT its burned-in title and captions, so the cover
    text doesn't sit on top of the clip's own title."""
    from .design import Design
    from .render import render_clip

    data = design.model_dump() if hasattr(design, "model_dump") else dict(design or {})
    data["title"] = {**(data.get("title") or {}), "show": False}
    data["caption"] = {**(data.get("caption") or {}), "style": "none"}
    data["shout"] = False
    return render_clip(source, max(0.0, at), max(0.0, at) + 0.2, output, Design(**data), [], None)


def make_cover(video, title: str, output=None, at: float | None = None, clean=None) -> Path:
    """Write the cover JPG and return its path. `video` is the finished clip (used to pick the
    frame). `clean` = (source video, clip start in the source, design): the frame is then
    re-made from the source without the clip's own title and captions."""
    from .engine import _run
    from .fonts import _load
    from .media import probe_media

    video = Path(video).resolve()
    output = Path(output or cover_path(video)).resolve()
    info = probe_media(video)
    width, height = info.width or 1080, info.height or 1920
    at = best_frame_time(video) if at is None else at
    title = " ".join(str(title or "").split())[:140]
    with tempfile.TemporaryDirectory(prefix="clipstudio-cover-") as temp_name:
        temp = Path(temp_name)
        if clean:
            try:
                source, start, design = clean
                video = clean_still(source, float(start) + at, design, temp / "still.mp4")
                info = probe_media(video)
                width, height, at = info.width or width, info.height or height, 0.0
            except Exception:  # noqa: BLE001 - fall back to the finished clip's own frame
                pass
        filters = []
        if title:
            font_path = _font_for(title)
            shutil.copy2(font_path, temp / "cover.ttf")
            font = _load(str(font_path))
            text = title.upper() if font_path.name.startswith("Anton") else title
            measure = (lambda s: font.width(s)) if font else (lambda s: len(s) * 0.5)
            vertical = height >= width
            box_w = width * 0.88
            size = round(height * (0.068 if vertical else 0.11))
            lines = wrap_title(text, measure, box_w / size)
            widest = max((measure(line) for line in lines), default=1) or 1
            size = int(min(size, box_w / widest))
            line_h = round(size * 1.12)
            top = round(height * (0.11 if vertical else 0.08))
            band = top + line_h * len(lines) + round(size * 0.6)
            # darker when the frame still has the clip's own burned-in title under it
            filters.append(f"drawbox=x=0:y=0:w=iw:h={band}:color=black@{0.45 if clean else 0.72}:t=fill")
            for i, line in enumerate(lines):
                (temp / f"line{i}.txt").write_text(line, encoding="utf-8")
                colour = YELLOW if i == len(lines) - 1 and len(lines) > 1 else "white"
                filters.append(
                    f"drawtext=fontfile=cover.ttf:textfile=line{i}.txt:fontsize={size}:fontcolor={colour}"
                    f":borderw={max(2, size // 12)}:bordercolor=black:shadowx=0:shadowy={max(1, size // 20)}"
                    f":shadowcolor=black@0.6:x=(w-text_w)/2:y={top + i * line_h}"
                )
        command = [find_tool("ffmpeg"), "-nostdin", "-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.3f}",
                   "-i", str(video), "-frames:v", "1", *(["-vf", ",".join(filters)] if filters else []),
                   "-q:v", "3", "cover.jpg"]
        _run(command, timeout=120, cwd=temp)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(temp / "cover.jpg", output)
    return output
