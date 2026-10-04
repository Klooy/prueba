"""Logo de la marca: máscara frontal de Anubis en oro (estilo cloisonné egipcio).

Genera el símbolo SVG `#anubis` y lo escribe en templates/_icons.html (entre los
marcadores anubis:start / anubis:end) y en static/img/favicon.svg.

    python tools/anubis_logo.py

Lienzo 120 x 134 (viewBox 12 0 96 134), eje de simetría x = 60. Las formas que
cruzan el eje se generan completas a partir de su mitad derecha; las laterales se
dibujan a la derecha y se reflejan con <use>.
"""
import math
import os
import re

W = 120
INK = "#050805"


def f(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def P(p):
    return f"{f(p[0])} {f(p[1])}"


def mir(p):
    return (W - p[0], p[1])


def sym(start, segs):
    """Mitad derecha desde `start` (sobre el eje) hasta un punto final sobre el eje."""
    norm, pts, cur = [], [start], start
    for s in segs:
        if s[0] == "L":
            c1, c2, p = cur, s[1], s[1]
        else:
            _, c1, c2, p = s
        norm.append((c1, c2, p))
        pts.append(p)
        cur = p
    d = [f"M{P(start)}"] + [f"C{P(c1)} {P(c2)} {P(p)}" for c1, c2, p in norm]
    for i in range(len(norm) - 1, -1, -1):
        c1, c2, _ = norm[i]
        d.append(f"C{P(mir(c2))} {P(mir(c1))} {P(mir(pts[i]))}")
    return " ".join(d) + "Z"


def polar(cx, cy, r, deg):
    a = math.radians(deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def sector(cx, cy, r1, r2, a0, a1):
    o0, o1 = polar(cx, cy, r2, a0), polar(cx, cy, r2, a1)
    i1, i0 = polar(cx, cy, r1, a1), polar(cx, cy, r1, a0)
    large = 1 if (a1 - a0) > 180 else 0
    return (f"M{P(o0)} A{f(r2)} {f(r2)} 0 {large} 1 {P(o1)} L{P(i1)} "
            f"A{f(r1)} {f(r1)} 0 {large} 0 {P(i0)}Z")


# ------------------------------------------------------------------ geometría
HEAD = sym((60, 33.4), [
    ("C", (62.8, 33.4), (65.2, 34), (67.2, 35.6)),
    ("C", (71.8, 40), (76.2, 46.2), (77.9, 52.6)),
    ("C", (78.9, 57.6), (77.4, 62.6), (73.6, 66.6)),
    ("C", (69.6, 70.6), (66.9, 76), (66.1, 84)),
    ("C", (65.4, 91), (64.9, 96.6), (63.9, 99.6)),
    ("C", (62.9, 102.2), (61.5, 103.2), (60, 103.2)),
])
NOSE = sym((60, 94.6), [
    ("C", (62.6, 94.6), (64.4, 95.3), (64.4, 97)),
    ("C", (64.4, 99.3), (62.1, 101.8), (60, 102.7)),
])
EAR = ("M64.2 37.2 C67.6 26 74.2 13 83 2.5 C88 15 89.6 30 85.6 47.4 "
       "C83.8 48.6 81.8 49.5 79.8 50 C75.8 45 70.6 40.4 64.2 37.2Z")
EAR_IN = ("M69 36.2 C72 28.6 76.7 18.8 82.3 11.2 C84.8 21.6 85.3 31.6 83.2 42.4 "
          "C81.6 43.3 80.1 43.9 78.7 44.2 C76 40.6 72.7 38 69 36.2Z")
WIG = "M37.5 44 L82.5 44 L91.5 116 L28.5 116Z"
LAPPET_EDGE = "M82.5 44 L91.5 116"
EYE_SOCKET = ("M62.7 57.9 C65.5 53.5 70.5 50.8 75.9 50.6 L78.8 50.3 L75.3 52.7 "
              "C72.6 56.6 67.6 59.7 62.7 57.9Z")
EYE_IRIS = "M64.5 57 C66.9 54.2 70.4 52.4 74.1 52.2 C71.9 55.2 68.1 57.8 64.5 57Z"
BROW = "M62.6 52.2 C66.6 49 72 47.4 77.9 47.7 L77.6 48.6 C72 48.6 67 50.1 63.1 52.8Z"
BRIDGE = "M62.3 60.8 C62 72 61.7 83 61.3 93"

COLLAR_C = (60, 78)
COLLAR_A = (38, 142)


def collar():
    cx, cy = COLLAR_C
    a0, a1 = COLLAR_A
    out = [f'<path d="{sector(cx, cy, 33, 54, a0 - 1.5, a1 + 1.5)}" fill="{INK}"/>']
    for r1, r2 in ((34.6, 40.4), (41.8, 46.8), (48.2, 52.6)):
        out.append(f'<path d="{sector(cx, cy, r1, r2, a0, a1)}" fill="url(#lg-collar)"/>')
    beads = []
    a = a0 + 3
    while a <= a1 - 2.9:
        p1, p2 = polar(cx, cy, 49.3, a), polar(cx, cy, 51.6, a)
        beads.append(f"M{P(p1)} L{P(p2)}")
        a += 4.2
    out.append(f'<path d="{" ".join(beads)}" stroke="{INK}" stroke-width="1.15" stroke-linecap="round"/>')
    # cuentas del anillo medio
    dots = []
    a = a0 + 4
    while a <= a1 - 3.9:
        x, y = polar(cx, cy, 44.3, a)
        dots.append(f'<circle cx="{f(x)}" cy="{f(y)}" r=".72"/>')
        a += 6.5
    out.append(f'<g fill="{INK}" opacity=".85">{"".join(dots)}</g>')
    return "\n    ".join(out)


def lappet_stripes():
    rects = []
    y = 45.0
    while y < 116:
        rects.append(f'<rect x="20" y="{f(y)}" width="80" height="3.9"/>')
        y += 6.7
    return "".join(rects)


def build():
    mirror = 'transform="matrix(-1 0 0 1 120 0)"'
    return f'''<defs>
    <linearGradient id="lg-ear" gradientUnits="userSpaceOnUse" x1="0" y1="2" x2="0" y2="48">
      <stop offset="0" stop-color="#fff2c2"/><stop offset=".45" stop-color="#e9bd57"/><stop offset="1" stop-color="#9a6a1c"/>
    </linearGradient>
    <radialGradient id="lg-face" gradientUnits="userSpaceOnUse" cx="60" cy="54" r="50" fx="60" fy="40">
      <stop offset="0" stop-color="#fff4cc"/><stop offset=".32" stop-color="#f3cd68"/>
      <stop offset=".68" stop-color="#c8912d"/><stop offset="1" stop-color="#7a5112"/>
    </radialGradient>
    <linearGradient id="lg-lap" gradientUnits="userSpaceOnUse" x1="0" y1="40" x2="0" y2="116">
      <stop offset="0" stop-color="#f6d47c"/><stop offset="1" stop-color="#a06e1d"/>
    </linearGradient>
    <linearGradient id="lg-collar" gradientUnits="userSpaceOnUse" x1="0" y1="96" x2="0" y2="134">
      <stop offset="0" stop-color="#ffe7a3"/><stop offset=".5" stop-color="#e2b04a"/><stop offset="1" stop-color="#94641a"/>
    </linearGradient>
    <radialGradient id="lg-jade" gradientUnits="objectBoundingBox" cx=".4" cy=".35" r=".8">
      <stop offset="0" stop-color="#c8ffdd"/><stop offset=".45" stop-color="#34d27a"/><stop offset="1" stop-color="#0c5a30"/>
    </radialGradient>
    <clipPath id="lg-wig-clip"><path d="{WIG}"/></clipPath>
    <linearGradient id="lg-ear-in" gradientUnits="userSpaceOnUse" x1="0" y1="10" x2="0" y2="46">
      <stop offset="0" stop-color="#8a5f17"/><stop offset="1" stop-color="#3e2806"/>
    </linearGradient>
    <g id="lg-ear-r">
      <path d="{EAR}" fill="url(#lg-ear)" stroke="{INK}" stroke-width="1.3" stroke-linejoin="round"/>
      <path d="{EAR_IN}" fill="url(#lg-ear-in)" stroke="{INK}" stroke-width=".9" stroke-linejoin="round"/>
    </g>
    <g id="lg-eye-r">
      <path d="{BROW}" fill="{INK}" transform="translate(0 1.6)"/>
      <g transform="translate(0 1.6)">
        <path d="{EYE_SOCKET}" fill="{INK}"/>
        <path d="{EYE_IRIS}" fill="url(#lg-jade)"/>
        <circle cx="69.6" cy="54.9" r="1.35" fill="{INK}"/>
        <circle cx="68.2" cy="54.3" r=".55" fill="#fff" opacity=".85"/>
      </g>
      <path d="{BRIDGE}" fill="none" stroke="{INK}" stroke-width=".7" stroke-linecap="round" opacity=".55"/>
    </g>
  </defs>
  <symbol id="anubis" viewBox="12 0 96 134">
    <path d="{WIG}" fill="{INK}"/>
    <g clip-path="url(#lg-wig-clip)" fill="url(#lg-lap)">{lappet_stripes()}</g>
    <path d="{LAPPET_EDGE}" fill="none" stroke="url(#lg-lap)" stroke-width=".9"/>
    <path d="{LAPPET_EDGE}" fill="none" stroke="url(#lg-lap)" stroke-width=".9" {mirror}/>
    <path d="{HEAD}" fill="url(#lg-face)" stroke="{INK}" stroke-width="1.4" stroke-linejoin="round"/>
    <use href="#lg-ear-r"/><use href="#lg-ear-r" {mirror}/>
    <use href="#lg-eye-r"/><use href="#lg-eye-r" {mirror}/>
    <path d="M60 37.5 C61.6 50 61.7 74 60.9 91.5 L59.1 91.5 C58.3 74 58.4 50 60 37.5Z" fill="#fff8de" opacity=".28"/>
    <path d="{NOSE}" fill="{INK}"/>
    <ellipse cx="60" cy="96.6" rx="2.3" ry=".9" fill="#fff" opacity=".18"/>
    {collar()}
    <g transform="translate(60 121.5)">
      <circle r="6.6" fill="url(#lg-collar)" stroke="{INK}" stroke-width="1.1"/>
      <circle r="5" fill="{INK}"/>
      <use href="#leaf7" transform="translate(0 3.6) scale(.105)" fill="url(#lg-jade)"/>
    </g>
  </symbol>'''


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    icons_path = os.path.join(root, "templates", "_icons.html")
    icons = open(icons_path, encoding="utf-8").read()
    start, end = "<!-- anubis:start -->", "<!-- anubis:end -->"
    block = f"{start}\n  {build()}\n  {end}"
    icons = re.sub(re.escape(start) + ".*?" + re.escape(end), lambda _: block, icons, flags=re.S)
    open(icons_path, "w", encoding="utf-8").write(icons)

    leaf = re.search(r'(<path id="lf".*?</g>)', icons, re.S).group(1)
    favicon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="12 0 96 134">'
        f"<defs>{leaf}</defs>{build()}"
        '<use href="#anubis" x="12" y="0" width="96" height="134"/></svg>\n'
    )
    open(os.path.join(root, "static", "img", "favicon.svg"), "w", encoding="utf-8").write(favicon)
    print("Logo actualizado en templates/_icons.html y static/img/favicon.svg")


if __name__ == "__main__":
    main()
