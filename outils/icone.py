"""Dessine l'icône d'Efface-moi : une gomme qui efface une ligne de données.

    pip install pillow
    python outils/icone.py

Écrit web/icone.ico (application, exe, raccourcis) et web/icone.png (page GitHub).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024                                # on dessine grand, puis on réduit : les bords sont lissés
OUT = Path(__file__).resolve().parent.parent / "web"
GREEN_TOP, GREEN_BOTTOM = (40, 128, 99), (20, 70, 55)
WHITE, MINT = (255, 255, 255), (150, 214, 189)


def background():
    grad = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        d.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(GREEN_TOP, GREEN_BOTTOM)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([32, 32, S - 32, S - 32], radius=215, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    return img


def data_line(img):
    """À gauche, le trait encore intact ; à droite, ce qu'il en reste après le passage de la gomme."""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y, half = 772, 36
    d.rounded_rectangle([150, y - half, 400, y + half], radius=half, fill=WHITE + (255,))
    for x, alpha, r in ((585, 150, 26), (705, 100, 21), (810, 60, 16)):
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE + (alpha,))
    return Image.alpha_composite(img, layer)


def eraser(img):
    w, h, angle = 500, 236, 35
    cx, cy = S / 2, S / 2
    left, top, right, bottom = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([left, top, right, bottom], radius=54, fill=MINT + (255,))          # fourreau
    d.rounded_rectangle([left, top, left + w * 0.42, bottom], radius=54, fill=WHITE + (255,))  # bout qui gomme
    d.rectangle([left + 60, top, left + w * 0.42, bottom], fill=WHITE + (255,))
    layer = layer.rotate(angle, resample=Image.BICUBIC, center=(cx, cy), translate=(118, -18))
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 95), (0, 0), layer.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(20)).transform((S, S), Image.AFFINE, (1, 0, -10, 0, 1, -22))
    clip = img.split()[3]                                   # l'ombre ne déborde pas du fond arrondi
    shadow.putalpha(Image.composite(shadow.split()[3], Image.new("L", (S, S), 0), clip))
    return Image.alpha_composite(Image.alpha_composite(img, shadow), layer)


def main():
    img = eraser(data_line(background()))
    OUT.mkdir(exist_ok=True)
    img.resize((256, 256), Image.LANCZOS).save(OUT / "icone.png", optimize=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [img.resize((n, n), Image.LANCZOS) for n in sizes]
    frames[-1].save(OUT / "icone.ico", format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
    print("écrit :", OUT / "icone.ico", "et", OUT / "icone.png")
    return img


if __name__ == "__main__":
    main()
