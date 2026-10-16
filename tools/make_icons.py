"""Draw Nestwork's app icon (nested squares, neon on dark) as PNGs, with no image library.

Run: python3 tools/make_icons.py nestwork/static/icons
"""
import struct, sys, zlib
from pathlib import Path

BG, CYAN, MAG, BLUE = (11, 13, 27), (20, 182, 212), (214, 54, 184), (84, 136, 246)

def blend(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

def draw(n):
    px = [[BG] * n for _ in range(n)]
    def frame(cx, cy, half, width, color, glow=0.0):
        for y in range(n):
            for x in range(n):
                dx, dy = abs(x + .5 - cx), abs(y + .5 - cy)
                d = max(dx, dy)                        # distance in "square" metric
                edge = abs(d - half)
                if edge <= width / 2:
                    px[y][x] = color
                elif glow and edge <= width / 2 + glow and d < half + width / 2 + glow:
                    t = 1 - (edge - width / 2) / glow
                    px[y][x] = blend(px[y][x], color, 0.35 * t * t)
    c = n / 2
    frame(c, c, n * .34, n * .055, CYAN, glow=n * .05)
    frame(c, c, n * .20, n * .05, MAG, glow=n * .04)
    # the innermost block, filled
    s = n * .075
    for y in range(n):
        for x in range(n):
            if abs(x + .5 - c) <= s and abs(y + .5 - c) <= s:
                px[y][x] = BLUE
    return px

def png(px, path):
    n = len(px)
    raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in px)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    Path(path).write_bytes(data)

out = Path(sys.argv[1])
for size in (180, 192, 512):
    png(draw(size), out / f"icon-{size}.png")
print("ok")
