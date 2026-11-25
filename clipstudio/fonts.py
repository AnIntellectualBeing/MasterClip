"""What a font can draw, and how wide text is, read straight from the .ttf/.ttc file.

Used for two things in the burned-in text:
  * Hindi, Kannada, Tamil, Arabic, ... aren't in the bundled display fonts (Anton, Luckiest Guy...).
    Those letters switch to a Windows font that has them (Nirmala UI covers every Indian script),
    so captions never show empty boxes.
  * The title box is drawn as a rounded shape behind the text, so it needs the text's width.
Only the tables we need are read (cmap, hmtx, hhea, head); kerning is ignored (a few px).
"""

from __future__ import annotations

import os
import struct
from functools import lru_cache
from pathlib import Path

from .design import FONT_DIR, FONTS

WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
# (first, last code point, family, file in C:\Windows\Fonts) - checked in order
FALLBACKS = [
    (0x0900, 0x0DFF, "Nirmala UI", "Nirmala.ttc"),       # Devanagari .. Sinhala (all Indian scripts)
    (0x0E00, 0x0EFF, "Leelawadee UI", "LeelawUI.ttf"),   # Thai, Lao
    (0x1100, 0x11FF, "Malgun Gothic", "malgunbd.ttf"),   # Hangul
    (0xAC00, 0xD7AF, "Malgun Gothic", "malgunbd.ttf"),
    (0x3040, 0x30FF, "Yu Gothic", "YuGothB.ttc"),        # Japanese kana
    (0x4E00, 0x9FFF, "Microsoft YaHei", "msyhbd.ttc"),   # CJK
]
DEFAULT_FALLBACK = ("Segoe UI", "segoeuib.ttf")          # Arabic, Cyrillic, Greek, accents...


class _Font:
    def __init__(self, data: bytes, offset: int = 0):
        tables = {}
        for i in range(struct.unpack(">H", data[offset + 4 : offset + 6])[0]):
            tag, _, start, length = struct.unpack(">4sIII", data[offset + 12 + 16 * i : offset + 28 + 16 * i])
            tables[tag] = (start, length)
        self.units = struct.unpack(">H", data[tables[b"head"][0] + 18 : tables[b"head"][0] + 20])[0] or 1000
        hhea = tables[b"hhea"][0]
        count = struct.unpack(">H", data[hhea + 34 : hhea + 36])[0]
        hmtx = tables[b"hmtx"][0]
        self.advances = [struct.unpack(">H", data[hmtx + 4 * i : hmtx + 4 * i + 2])[0] for i in range(count)]
        self.cmap = self._cmap(data, tables[b"cmap"][0])

    @staticmethod
    def _cmap(data: bytes, base: int) -> dict[int, int]:
        best = None
        for i in range(struct.unpack(">H", data[base + 2 : base + 4])[0]):
            platform, encoding, offset = struct.unpack(">HHI", data[base + 4 + 8 * i : base + 12 + 8 * i])
            fmt = struct.unpack(">H", data[base + offset : base + offset + 2])[0]
            if platform in (0, 3) and fmt in (4, 12) and (best is None or fmt == 12):
                best = (fmt, base + offset)
        out: dict[int, int] = {}
        if not best:
            return out
        fmt, at = best
        if fmt == 12:
            groups = struct.unpack(">I", data[at + 12 : at + 16])[0]
            for g in range(groups):
                first, last, glyph = struct.unpack(">III", data[at + 16 + 12 * g : at + 28 + 12 * g])
                for code in range(first, min(last, first + 70000) + 1):
                    out[code] = glyph + code - first
            return out
        segs = struct.unpack(">H", data[at + 6 : at + 8])[0] // 2
        ends = struct.unpack(f">{segs}H", data[at + 14 : at + 14 + 2 * segs])
        starts_at = at + 16 + 2 * segs
        starts = struct.unpack(f">{segs}H", data[starts_at : starts_at + 2 * segs])
        deltas = struct.unpack(f">{segs}h", data[starts_at + 2 * segs : starts_at + 4 * segs])
        ranges_at = starts_at + 4 * segs
        offsets = struct.unpack(f">{segs}H", data[ranges_at : ranges_at + 2 * segs])
        for s in range(segs):
            for code in range(starts[s], ends[s] + 1):
                if code == 0xFFFF:
                    continue
                if offsets[s] == 0:
                    glyph = (code + deltas[s]) & 0xFFFF
                else:
                    pos = ranges_at + 2 * s + offsets[s] + 2 * (code - starts[s])
                    glyph = struct.unpack(">H", data[pos : pos + 2])[0]
                    if glyph:
                        glyph = (glyph + deltas[s]) & 0xFFFF
                if glyph:
                    out[code] = glyph
        return out

    def has(self, ch: str) -> bool:
        return ord(ch) in self.cmap

    def width(self, text: str) -> float:
        """Advance width in em."""
        total = 0
        for ch in text:
            glyph = self.cmap.get(ord(ch), 0)
            total += self.advances[min(glyph, len(self.advances) - 1)] if self.advances else self.units / 2
        return total / self.units


@lru_cache(maxsize=32)
def _load(path: str) -> _Font | None:
    try:
        data = Path(path).read_bytes()
        offset = 0
        if data[:4] == b"ttcf":
            offset = struct.unpack(">I", data[12:16])[0]  # first face of a collection
        return _Font(data, offset)
    except (OSError, struct.error, KeyError, IndexError):
        return None


def _family_name(path: Path) -> tuple[str, str] | None:
    """(family, subfamily) from a .ttf/.otf name table, reading only the bytes needed."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or head[:4] == b"ttcf":
                return None
            count = struct.unpack(">H", head[4:6])[0]
            directory = fh.read(16 * count)
            name_at = None
            for i in range(count):
                tag, _, start, length = struct.unpack(">4sIII", directory[16 * i: 16 * i + 16])
                if tag == b"name":
                    name_at = (start, length)
            if not name_at:
                return None
            fh.seek(name_at[0])
            table = fh.read(min(name_at[1], 200_000))
        records, strings = struct.unpack(">H", table[2:4])[0], struct.unpack(">H", table[4:6])[0]
        found: dict[int, str] = {}
        for i in range(records):
            platform, encoding, language, name_id, length, offset = struct.unpack(
                ">HHHHHH", table[6 + 12 * i: 18 + 12 * i])
            if name_id not in (1, 2) or name_id in found:
                continue
            raw = table[strings + offset: strings + offset + length]
            if platform == 3 and language in (0x409, 0):
                found[name_id] = raw.decode("utf-16-be", "ignore")
            elif platform == 1 and language == 0:
                found.setdefault(name_id, raw.decode("latin-1", "ignore"))
        family = (found.get(1) or "").strip()
        return (family, (found.get(2) or "").strip()) if family else None
    except (OSError, struct.error, IndexError):
        return None


@lru_cache(maxsize=1)
def installed_fonts() -> dict[str, Path]:
    """{family: file} for the fonts installed on this PC (Windows + the user's own fonts folder).
    Picks the Regular file of each family; font collections (.ttc) are skipped."""
    folders = [WINDOWS_FONTS]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        folders.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    out: dict[str, tuple[Path, bool]] = {}
    for folder in folders:
        try:
            files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".ttf", ".otf"))
        except OSError:
            continue
        for path in files:
            named = _family_name(path)
            if not named:
                continue
            family, sub = named
            if family in FONTS or family.startswith(".") or len(family) > 60:
                continue
            regular = sub.lower() in ("regular", "normal", "book", "roman")
            if family not in out or (regular and not out[family][1]):
                out[family] = (path, regular)
    return {k: v[0] for k, v in sorted(out.items(), key=lambda kv: kv[0].lower())}


def font_file(family: str) -> Path | None:
    """The file behind a family we may use: bundled fonts/, a Windows fallback or an installed font."""
    if family in FONTS:
        return FONT_DIR / FONTS[family]
    installed = installed_fonts().get(family)
    if installed and family not in {f[2] for f in FALLBACKS} and family != DEFAULT_FALLBACK[0]:
        return installed
    for *_, name, file in FALLBACKS:
        if name == family:
            path = WINDOWS_FONTS / file
            return path if path.is_file() else None
    if family == DEFAULT_FALLBACK[0]:
        path = WINDOWS_FONTS / DEFAULT_FALLBACK[1]
        return path if path.is_file() else None
    return None


def _font(family: str) -> _Font | None:
    path = font_file(family)
    return _load(str(path)) if path else None


def fallback_for(ch: str) -> str:
    code = ord(ch)
    for first, last, family, _ in FALLBACKS:
        if first <= code <= last:
            return family
    return DEFAULT_FALLBACK[0]


def needs_fallback(ch: str, family: str) -> bool:
    if ch.isspace() or ord(ch) < 0x80:
        return False
    font = _font(family)
    return font is None or not font.has(ch)


def runs(text: str, family: str) -> list[tuple[str, str]]:
    """[(family, piece)]: the text split where letters must come from another font."""
    out: list[tuple[str, str]] = []
    for ch in text:
        use = fallback_for(ch) if needs_fallback(ch, family) else family
        # spaces and marks stay with the run they're in (no font switch for a space)
        if out and (ch.isspace() or use == out[-1][0]):
            out[-1] = (out[-1][0], out[-1][1] + ch)
        else:
            out.append((use, ch))
    return out


def families_used(texts, family: str) -> set[str]:
    used = {family}
    for text in texts:
        for fam, _ in runs(text, family):
            used.add(fam)
    return used


def text_width(text: str, family: str, size_px: float) -> float:
    """Width in pixels at a CSS-style size (em = size_px), with fallback letters measured too."""
    total = 0.0
    for fam, piece in runs(text, family):
        font = _font(fam)
        total += (font.width(piece) if font else 0.55 * len(piece)) * size_px
    return total


def wrap(text: str, family: str, size_px: float, max_px: float) -> list[str]:
    """Greedy word wrap to lines no wider than max_px (a single long word stays whole)."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and text_width(trial, family, size_px) > max_px:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines or [""]
