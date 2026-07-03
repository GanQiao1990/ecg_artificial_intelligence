#!/usr/bin/env python3
"""Generate ECG AI application icons (PNG + ICO) for window title and PyInstaller."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 256


def _draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)

    margin = size * 0.12
    inner = size - 2 * margin
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size * 0.18,
        fill=(30, 41, 59, 255),
        outline=(56, 189, 248, 255),
        width=max(2, size // 64),
    )

    baseline = size * 0.56
    points = [
        (margin + inner * 0.05, baseline),
        (margin + inner * 0.18, baseline),
        (margin + inner * 0.24, baseline - inner * 0.10),
        (margin + inner * 0.30, baseline + inner * 0.22),
        (margin + inner * 0.36, baseline - inner * 0.42),
        (margin + inner * 0.42, baseline + inner * 0.30),
        (margin + inner * 0.48, baseline - inner * 0.12),
        (margin + inner * 0.54, baseline),
        (margin + inner * 0.95, baseline),
    ]
    draw.line(points, fill=(34, 197, 94, 255), width=max(3, size // 28), joint="curve")

    draw.ellipse(
        (size * 0.72, size * 0.18, size * 0.86, size * 0.32),
        fill=(239, 68, 68, 255),
    )
    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = _draw_icon(SIZE)
    png_path = ASSETS / "app_icon.png"
    ico_path = ASSETS / "app_icon.ico"
    master.save(png_path, format="PNG")
    master.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()