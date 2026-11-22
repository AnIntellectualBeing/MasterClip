"""The buyer's own templates ("Save as my template"), kept in <data>/my-templates.json."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from .design import Design

MAX_TEMPLATES = 60


class MyTemplates:
    def __init__(self, root: Path):
        self.path = Path(root) / "my-templates.json"
        self.lock = threading.Lock()

    def all(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [t for t in data if isinstance(t, dict) and t.get("id") and isinstance(t.get("design"), dict)]

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_suffix(".part")
        part.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        part.replace(self.path)

    @staticmethod
    def _clean(name: str, design: dict, kind: str) -> tuple[str, dict, str]:
        name = " ".join(str(name or "").split())[:40]
        if not name:
            raise ValueError("Give your template a name.")
        checked = Design(**design).model_dump()
        # Things that belong to one video, not to a template.
        for key in ("cam_roi", "game_roi", "credit"):
            checked.pop(key, None)
        checked["title"].pop("text", None)
        kind = "long" if checked["aspect"] == "16:9" else "short"
        return name, checked, kind

    def add(self, name: str, design: dict, base: str = "", length: int = 30) -> dict:
        name, checked, kind = self._clean(name, design, "")
        with self.lock:
            items = self.all()
            if len(items) >= MAX_TEMPLATES:
                raise ValueError(f"You can keep up to {MAX_TEMPLATES} templates. Delete one first.")
            item = {
                "id": "my-" + uuid.uuid4().hex[:10], "kind": kind, "name": name, "mine": True,
                "blurb": "Your template", "base": str(base or "")[:40], "length": int(length or 30),
                "needs_cam": checked["layout"] in ("stack", "corner"), "design": checked,
            }
            checked["template"] = item["id"]
            items.append(item)
            self._write(items)
            return item

    def update(self, ident: str, name: str | None = None, design: dict | None = None) -> dict:
        with self.lock:
            items = self.all()
            item = next((t for t in items if t["id"] == ident), None)
            if not item:
                raise KeyError(ident)
            if design is not None or name is not None:
                new_name, checked, kind = self._clean(name if name is not None else item["name"],
                                                      design if design is not None else item["design"], "")
                checked["template"] = ident
                item.update(name=new_name, design=checked, kind=kind,
                            needs_cam=checked["layout"] in ("stack", "corner"))
            self._write(items)
            return item

    def delete(self, ident: str) -> None:
        with self.lock:
            items = self.all()
            kept = [t for t in items if t["id"] != ident]
            if len(kept) == len(items):
                raise KeyError(ident)
            self._write(kept)
