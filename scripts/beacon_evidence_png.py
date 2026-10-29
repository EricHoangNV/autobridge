"""Render EVIDENCE.md as a terminal-style PNG for the deck (REQ-025 6b).

Best-effort: exits nonzero when pillow is unavailable (the shell caller
falls back to markdown-only). Reads markdown, strips formatting fences,
renders white monospace on near-black.
"""

from __future__ import annotations

import pathlib
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: beacon_evidence_png.py EVIDENCE.md evidence.png", flush=True)
        return 2
    src = pathlib.Path(argv[1])
    dst = pathlib.Path(argv[2])
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except ImportError:
        print("beacon_evidence_png: pillow unavailable", flush=True)
        return 1
    lines: list[str] = []
    for raw in src.read_text(encoding="utf-8").splitlines():
        line = raw.strip("`").rstrip()
        if line.startswith("#"):
            line = line.lstrip("# ").upper()
        lines.append(line[:110])
    font = ImageFont.load_default()
    try:
        mono = ImageFont.truetype("DejaVuSansMono.ttf", 15)
    except OSError:
        mono = font
    widths = [int(ImageDraw.Draw(Image.new("RGB", (8, 8))).textlength(line, font=mono)) for line in lines]
    width = max(widths + [100]) + 48
    height = 24 + 20 * len(lines) + 24
    img = Image.new("RGB", (width, height), (12, 14, 18))
    draw = ImageDraw.Draw(img)
    y = 24
    for line in lines:
        draw.text((24, y), line, font=mono, fill=(232, 237, 243))
        y += 20
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)
    print(f"beacon_evidence_png: wrote {dst} ({width}x{height})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
