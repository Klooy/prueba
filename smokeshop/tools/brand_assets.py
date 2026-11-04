"""Genera las imágenes de marca a partir de la ilustración de Anubis.

    python tools/brand_assets.py ruta/a/anubis.jpg

Crea en static/img/:
  anubis.jpg       ilustración para la portada (máx. 1024 px)
  anubis-logo.jpg  recorte cuadrado de la cabeza para el encabezado (192 px)

Los íconos de la pestaña y de las apps instalables son la silueta discreta de Anubis: salen de
tools/app_icons.py.
"""
import os
import sys

from PIL import Image

# Recorte de la cabeza (fracciones del ancho/alto de la imagen original).
HEAD_BOX = (0.22, 0.03, 0.78, 0.59)


def main(src):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "static", "img")
    os.makedirs(out_dir, exist_ok=True)

    img = Image.open(src).convert("RGB")
    hero = img.copy()
    hero.thumbnail((1024, 1024), Image.LANCZOS)
    hero.save(os.path.join(out_dir, "anubis.jpg"), quality=86, optimize=True, progressive=True)

    w, h = img.size
    l, t, r, b = HEAD_BOX
    head = img.crop((round(l * w), round(t * h), round(r * w), round(b * h)))
    head.resize((192, 192), Image.LANCZOS).save(
        os.path.join(out_dir, "anubis-logo.jpg"), quality=88, optimize=True)
    print(f"Imágenes generadas en static/img/ a partir de {src} ({w}x{h})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
