"""Draws the app icon (orange rounded square + white play triangle) as a multi-size .ico.

python packaging/make_icon.py  ->  packaging/clipstudio.ico
"""

import struct
from pathlib import Path

import cv2
import numpy as np

BRAND = (60, 106, 238)  # BGR of #EE6A3C


def draw(size: int) -> bytes:
    scale = 4
    big = size * scale
    img = np.zeros((big, big, 4), np.uint8)
    pad = int(big * 0.06)
    radius = int(big * 0.22)
    colour = (*BRAND, 255)
    x0, y0, x1, y1 = pad, pad, big - pad, big - pad
    cv2.rectangle(img, (x0 + radius, y0), (x1 - radius, y1), colour, -1)
    cv2.rectangle(img, (x0, y0 + radius), (x1, y1 - radius), colour, -1)
    for cx, cy in ((x0 + radius, y0 + radius), (x1 - radius, y0 + radius), (x0 + radius, y1 - radius), (x1 - radius, y1 - radius)):
        cv2.circle(img, (cx, cy), radius, colour, -1, cv2.LINE_AA)
    c = big / 2
    tri = np.array([[c - big * 0.14, c - big * 0.2], [c - big * 0.14, c + big * 0.2], [c + big * 0.22, c]], np.int32)
    cv2.fillPoly(img, [tri], (255, 255, 255, 255), cv2.LINE_AA)
    small = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", small)
    assert ok
    return png.tobytes()


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for size, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out = Path(__file__).with_name("clipstudio.ico")
    out.write_bytes(header + entries + blobs)
    cv2.imwrite(str(Path(__file__).with_name("icon-preview.png")), cv2.imdecode(np.frombuffer(images[-1], np.uint8), cv2.IMREAD_UNCHANGED))
    print(out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
