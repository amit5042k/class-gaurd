"""Draws the ClassGuard icon (shield with a camera lens) as PNG and ICO."""
import os
import sys

from PIL import Image, ImageDraw


def draw(size: int) -> Image.Image:
    s = size / 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8 * s, 8 * s, 248 * s, 248 * s], radius=52 * s, fill=(232, 64, 95))
    shield = [(128, 40), (196, 66), (196, 128), (184, 172), (128, 216), (72, 172), (60, 128),
              (60, 66)]
    d.polygon([(x * s, y * s) for x, y in shield], fill=(22, 24, 44))
    d.ellipse([96 * s, 92 * s, 160 * s, 156 * s], fill=(255, 255, 255))
    d.ellipse([112 * s, 108 * s, 144 * s, 140 * s], fill=(232, 64, 95))
    d.ellipse([121 * s, 117 * s, 135 * s, 131 * s], fill=(22, 24, 44))
    return img


def main(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    big = draw(256)
    big.save(os.path.join(out_dir, "icon.png"))
    big.save(os.path.join(out_dir, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         os.path.join(os.path.dirname(__file__), "..", "app", "assets"))
