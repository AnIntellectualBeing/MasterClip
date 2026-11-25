"""Moment mix (Balanced / Action / Dialogue) and "MasterClip learns what you like".

Each star rating goes to <data>/feedback.jsonl with what made the moment stand out (the
picker's signal parts). Once 8 or more different moments are rated, the signals that the
buyer's high-rated clips share count a little more, and those in low-rated clips a little
less. Everything stays on this PC; the change is small (at most +-35 %) and explained.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .moments import WEIGHTS

MIXES = {
    "balanced": {},
    # chases, crashes, loud jumps, big scene changes
    "action": {"event": 1.25, "jump": 1.6, "loud": 1.6, "peaks": 1.5, "scene": 1.6, "talk": 0.5},
    # back-and-forth talk, jokes, laughs
    "dialogue": {"talk": 2.2, "event": 1.1, "jump": 0.6, "loud": 0.5, "peaks": 0.5, "scene": 0.5},
}
NEEDED = 8
MAX_NUDGE = 0.35
NAMES = {
    "event": "named events (chases, crashes, fights)",
    "talk": "funny back-and-forth talk",
    "jump": "loud reactions",
    "loud": "loud parts",
    "peaks": "sudden bangs",
    "scene": "big on-screen changes",
    "heat": "the most replayed parts",
    "chat": "moments where chat went wild",
}
# Picker reasons -> the signal behind them (for clips made before parts were stored).
_REASON_PART = {
    "loud reaction": "jump", "big on-screen change": "scene", "most replayed": "heat",
    "chat went wild": "chat", "funny back-and-forth": "talk", "high audio energy": "loud",
}
_lock = threading.Lock()


def mix_weights(mix: str | None) -> dict:
    factors = MIXES.get(mix or "balanced", {})
    return {k: v * factors.get(k, 1.0) for k, v in WEIGHTS.items()}


def parts_from_reasons(reasons) -> dict:
    parts = {}
    for reason in reasons or ():
        key = _REASON_PART.get(str(reason).lower())
        if key:
            parts[key] = 1.0
        elif reason and str(reason).lower() not in ("activity", "you pressed clip now"):
            parts["event"] = 1.0  # the picker's event labels ("police chase", "crash", ...)
    return parts


def _path(root) -> Path:
    return Path(root) / "feedback.jsonl"


def record(root, job_id: str, clip: dict, stars: int) -> None:
    if not 1 <= int(stars) <= 5:
        raise ValueError("Pick 1 to 5 stars.")
    parts = clip.get("parts") or parts_from_reasons(clip.get("reasons"))
    row = {
        "t": round(time.time()), "job": job_id, "moment": f"{job_id}:{round(float(clip.get('start', 0)))}",
        "stars": int(stars), "parts": {k: round(float(v), 3) for k, v in parts.items() if k in WEIGHTS},
        "reasons": list(clip.get("reasons") or [])[:6], "title": str(clip.get("title") or "")[:90],
    }
    with _lock:
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def ratings(root) -> list[dict]:
    """The latest rating of each moment (re-rating a clip replaces its old stars)."""
    latest = {}
    try:
        lines = _path(root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
            if 1 <= int(row["stars"]) <= 5 and row.get("moment"):
                latest[row["moment"]] = row
        except (ValueError, KeyError, TypeError):
            continue
    return list(latest.values())


def learned(root) -> dict:
    """{rated, needed, active, factors {part: x}, notes [plain sentences]}"""
    rows = ratings(root)
    out = {"rated": len(rows), "needed": NEEDED, "active": False, "factors": {}, "notes": []}
    if len(rows) < NEEDED:
        return out
    mean = sum(r["stars"] for r in rows) / len(rows)
    factors = {}
    for part in WEIGHTS:
        values = [float((r.get("parts") or {}).get(part, 0)) for r in rows]
        avg = sum(values) / len(values)
        spread = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
        if spread < 0.05:
            continue  # this signal looks the same in every rated clip: nothing to learn
        # How much more (or less) the clips with this signal were liked, -1..1.
        lean = sum((r["stars"] - mean) / 2 * (v - avg) for r, v in zip(rows, values)) / len(rows) / spread
        lean = max(-1.0, min(1.0, lean))
        if abs(lean) >= 0.1:
            factors[part] = round(1 + MAX_NUDGE * lean, 3)
    out.update(active=True, factors=factors)
    for part, f in sorted(factors.items(), key=lambda kv: -abs(kv[1] - 1)):
        out["notes"].append(f"You like {NAMES[part]}: they count a bit {'more' if f > 1 else 'less'} now.")
    if not factors:
        out["notes"].append("Your ratings don't lean one way yet, so moments are picked as usual.")
    return out


def moment_weights(root, mix: str | None = None) -> dict | None:
    """Weights for moments.timeline(), or None when nothing changes (Balanced, nothing learned)."""
    weights = mix_weights(mix)
    for part, f in learned(root)["factors"].items():
        weights[part] = weights[part] * f
    return None if weights == WEIGHTS else weights
