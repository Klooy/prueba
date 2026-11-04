"""Íconos de las apps instalables: la silueta de Anubis (discreta, sin hojas ni humo).

    python tools/app_icons.py

Crea en static/img/app/:
  shop-*.png    app de los clientes (Anubis Shop): silueta dorada sobre negro
  panel-*.png   app del panel: los mismos colores al revés, para distinguirlas en el celular
  anubis-silueta.svg  la silueta sola, por si se quiere rehacer o usar en otro tamaño

Cada app lleva: 180 (iPhone), 192 y 512 (Android), maskable-192/512 (Android recorta el
ícono en círculo o en otra forma, así que la silueta va más pequeña) y 32 (pestaña del navegador).
Solo usa Pillow: las curvas de la silueta se convierten en polígonos y se dibujan en grande.
"""
import os
import re

from PIL import Image, ImageChops, ImageDraw

# Silueta mirando a la derecha, en un lienzo de 512: oreja, ojo, hocico y mandíbula...
FACE = """M262 18 C270 68 290 128 300 172 C310 182 320 190 332 196 L444 238
C462 242 471 252 465 264 C461 270 454 273 444 274 C432 276 424 282 416 286
C388 296 360 306 332 316 C316 304 302 288 295 264 C291 248 288 234 284 224
C272 206 257 192 246 176 C236 130 244 70 262 18 Z"""
EYE = "M300 212 C310 202 328 201 342 208 C332 220 314 223 302 219 L294 222 Z"
# ...y la peluca egipcia (nemes) con sus franjas.
WIG = """M236 186 C250 200 266 214 274 228 C280 244 282 264 288 282 C294 300 304 316 316 328
L322 470 L106 470 C96 380 110 280 150 222 C166 202 182 190 196 186 C212 182 226 182 236 186 Z"""
STRIPES = (300, 342, 384, 426)      # franjas de la peluca (alto 9)
BOX = (104, 18, 472, 470)           # caja de la silueta, para centrarla

DARK, GOLD = "#050705", "#e8b84a"
SUPER = 4                           # se dibuja 4 veces más grande y se reduce: bordes suaves


def polygon(path, steps=24):
    """Puntos de un camino SVG con M, L, C y Z absolutos (las curvas, en tramos rectos)."""
    tokens = re.findall(r"[MLCZ]|-?\d+(?:\.\d+)?", path)
    points, i, cmd = [], 0, None
    while i < len(tokens):
        if tokens[i] in "MLCZ":
            cmd = tokens[i]
            i += 1
            if cmd == "Z":
                continue
        if cmd in ("M", "L"):
            points.append((float(tokens[i]), float(tokens[i + 1])))
            i += 2
        elif cmd == "C":
            (x0, y0), (x1, y1, x2, y2, x3, y3) = points[-1], map(float, tokens[i:i + 6])
            for k in range(1, steps + 1):
                t = k / steps
                a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
                points.append((a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3))
            i += 6
    return points


def silhouette_mask(px, scale, stripes=True):
    """Máscara (L) de la silueta centrada en un cuadro de `px` y del tamaño `scale` (1 = lienzo)."""
    cx, cy = (BOX[0] + BOX[2]) / 2, (BOX[1] + BOX[3]) / 2
    k = px / 512

    def fit(points):
        return [(((x - cx) * scale + 256) * k, ((y - cy) * scale + 256) * k) for x, y in points]

    face = Image.new("L", (px, px), 0)
    draw = ImageDraw.Draw(face)
    draw.polygon(fit(polygon(FACE)), fill=255)
    draw.polygon(fit(polygon(EYE)), fill=0)
    wig = Image.new("L", (px, px), 0)
    draw = ImageDraw.Draw(wig)
    draw.polygon(fit(polygon(WIG)), fill=255)
    for y in STRIPES if stripes else ():
        (x0, y0), (x1, y1) = fit([(0, y), (512, y + 9)])
        draw.rectangle((x0, y0, x1, y1), fill=0)
    return ImageChops.lighter(face, wig)


def icon(size, bg, fg, scale, stripes=True, radius=0):
    """Ícono cuadrado: fondo `bg` (con esquinas redondeadas si `radius`, en px de 512) y silueta `fg`."""
    px = size * SUPER
    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(out).rounded_rectangle((0, 0, px - 1, px - 1), radius=radius * px / 512, fill=bg)
    out.paste(Image.new("RGBA", (px, px), fg), (0, 0), silhouette_mask(px, scale, stripes))
    out = out.resize((size, size), Image.LANCZOS)
    return out if radius else out.convert("RGB")      # en el celular, sin transparencia


def svg():
    cx, cy = (BOX[0] + BOX[2]) / 2, (BOX[1] + BOX[3]) / 2
    bands = "".join(f'<rect x="0" y="{y}" width="512" height="9"/>' for y in STRIPES)
    clean = lambda d: " ".join(d.split())            # noqa: E731
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<defs><mask id="franjas" maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">'
        f'<rect width="512" height="512" fill="#fff"/><g fill="#000">{bands}</g></mask></defs>'
        f'<g fill="{GOLD}" transform="translate({256 - cx} {256 - cy})">'
        f'<path fill-rule="evenodd" d="{clean(FACE)} {clean(EYE)}"/>'
        f'<path mask="url(#franjas)" d="{clean(WIG)}"/></g></svg>\n'
    )


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "static", "img", "app")
    os.makedirs(out_dir, exist_ok=True)
    for name, bg, fg in (("shop", DARK, GOLD), ("panel", GOLD, DARK)):
        for size in (180, 192, 512):
            icon(size, bg, fg, 0.84).save(os.path.join(out_dir, f"{name}-{size}.png"), optimize=True)
        for size in (192, 512):
            icon(size, bg, fg, 0.66).save(os.path.join(out_dir, f"{name}-maskable-{size}.png"), optimize=True)
        icon(32, bg, fg, 0.98, stripes=False, radius=96).save(os.path.join(out_dir, f"{name}-32.png"), optimize=True)
    with open(os.path.join(out_dir, "anubis-silueta.svg"), "w") as fh:
        fh.write(svg())
    print(f"Íconos generados en {out_dir}")


if __name__ == "__main__":
    main()
