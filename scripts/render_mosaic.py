#!/usr/bin/env python3
"""Render the PSN platinum mosaic for the profile README -- no browser involved.

Data comes from psnplathub's public JSON API (plats + profile); cover art and the
avatar are PlayStation's own CDN images referenced by that data. The image itself is
drawn here with Pillow, so layout, colours and fonts are ours to choose.

Runs daily in CI, but only re-renders when the trophy data or this script changes
(the hash covers both), so a no-op day produces no commit. Rarity % is deliberately
left out of the hash so its daily drift does not trigger commits.
"""
import hashlib
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

PSN_ID = "firefox15499"
TZ = ZoneInfo("Asia/Hong_Kong")   # earned dates are shown in this timezone
OUT = "assets/psn-mosaic.png"
HASH_FILE = "assets/.mosaic-hash"
ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")
FONT = os.path.join(ASSETS, "fonts", "Geist.ttf")
ICON_DIR = os.path.join(ASSETS, "trophy-icons")   # high-res icons as served by psnplathub.com

# ---- style ------------------------------------------------------------------
S = 2                      # supersampling / output scale (output is ~3050px wide)
COLS = 6
TILE = 232                 # px at 1x
GAP = 14
PAD = 32
HEADER_H = 150
RADIUS = 12

HEADER_BG = (0, 112, 204)  # #0070cc
PAGE_BG = (255, 255, 255)
HEADER_TEXT = (255, 255, 255)
HEADER_SUB = (207, 224, 245)

# PSN rarity tiers: 0 ultra rare, 1 very rare, 2 rare, 3 common
RARITY_COLOR = {0: (200, 160, 255), 1: (255, 212, 110), 2: (120, 200, 255), 3: (215, 222, 228)}
PLATFORM_COLOR = (120, 185, 255)


def font(size, weight):
    f = ImageFont.truetype(FONT, int(size * S))
    f.set_variation_by_axes([weight])
    return f


def get_bytes(url, tries=3):
    url = url.replace("http://", "https://")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "psn-mosaic-bot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def get_json(url):
    return json.loads(get_bytes(url))


def get_image(url):
    return Image.open(io.BytesIO(get_bytes(url))).convert("RGBA")


def load_data():
    base = "https://psnplathub.com/api"
    plats = get_json(f"{base}/plats?psnId={PSN_ID}")["plattedGames"]
    prof = get_json(f"{base}/profile?psnId={PSN_ID}")["profile"]
    plats.sort(key=lambda g: g.get("platEarnedDate") or "", reverse=True)  # newest first
    return plats, prof


def signature(plats, prof):
    with open(__file__, "rb") as f:
        script = f.read()
    payload = {
        "plats": [(g["npCommunicationId"], g.get("platEarnedDate"), g.get("platform"),
                   g.get("titleIconUrl")) for g in plats],
        "counts": prof.get("trophyCounts"),
        "level": prof.get("trophyLevel"),
        "avatar": prof.get("avatarUrl"),
        "script": hashlib.sha256(script).hexdigest(),
        "icons": [hashlib.sha256(open(os.path.join(ICON_DIR, f"{k}.png"), "rb").read()).hexdigest()
                  for k in ("platinum", "gold", "silver", "bronze", "total")],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# ---- drawing helpers ----------------------------------------------------------
def sc(v):
    return int(round(v * S))


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


def draw_text(d, xy, text, f, fill, anchor="la"):
    d.text(xy, text, font=f, fill=fill, anchor=anchor)


def trophy(img, kind, cx, cy, box_w, box_h):
    """Paste a trophy icon, aspect preserved, fitted inside a box centred at (cx, cy)."""
    ico = Image.open(os.path.join(ICON_DIR, f"{kind}.png")).convert("RGBA")
    k = min(sc(box_w) / ico.width, sc(box_h) / ico.height)
    ico = ico.resize((max(1, round(ico.width * k)), max(1, round(ico.height * k))), Image.LANCZOS)
    img.alpha_composite(ico, (sc(cx) - ico.width // 2, sc(cy) - ico.height // 2))


def fit_cover(src, size):
    """Fit `src` into a size x size tile without distorting it.

    Square art fills the tile. Other ratios are shown whole (contained) over a blurred,
    darkened copy of the same image, so there are no stretched pixels and no dead bars.
    """
    if abs(src.width / src.height - 1) < 0.02:
        return src.resize((size, size), Image.LANCZOS)
    k = max(size / src.width, size / src.height)  # cover, for the backdrop
    bg = src.resize((round(src.width * k), round(src.height * k)), Image.LANCZOS)
    left, top = (bg.width - size) // 2, (bg.height - size) // 2
    bg = bg.crop((left, top, left + size, top + size)).filter(ImageFilter.GaussianBlur(size / 14))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    k = min(size / src.width, size / src.height)  # contain, for the art
    fg = src.resize((round(src.width * k), round(src.height * k)), Image.LANCZOS)
    bg.alpha_composite(fg, ((size - fg.width) // 2, (size - fg.height) // 2))
    return bg


def pill(img, x0, y0, x1, y1, alpha=205):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([sc(x0), sc(y0), sc(x1), sc(y1)], sc((y1 - y0) / 2),
                                            fill=(10, 14, 22, alpha))
    img.alpha_composite(layer)


def header(img, prof):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, img.width, sc(HEADER_H)], fill=HEADER_BG)

    # avatar
    av = sc(96)
    a = get_image(prof["avatarUrl"]).resize((av, av), Image.LANCZOS)
    m = Image.new("L", (av, av), 0)
    ImageDraw.Draw(m).ellipse([0, 0, av - 1, av - 1], fill=255)
    img.paste(a, (sc(PAD), sc((HEADER_H - 96) / 2)), m)

    x = PAD + 96 + 22
    draw_text(d, (sc(x), sc(HEADER_H / 2 - 4)), prof["onlineId"], font(40, 650), HEADER_TEXT, "ls")
    draw_text(d, (sc(x + 2), sc(HEADER_H / 2 + 30)), f"LEVEL {prof['trophyLevel']}",
              font(18, 550), HEADER_SUB, "ls")

    # trophy counts, right-aligned
    c = prof["trophyCounts"]
    stats = [("platinum", c["platinum"]), ("gold", c["gold"]), ("silver", c["silver"]),
             ("bronze", c["bronze"]), ("total", sum(c.values()))]
    f = font(32, 650)
    col_w = 92
    right = img.width / S - PAD
    for i, (kind, n) in enumerate(reversed(stats)):
        cx = right - col_w / 2 - i * col_w
        trophy(img, kind, cx, HEADER_H / 2 - 20, 56, 56)
        d = ImageDraw.Draw(img)
        draw_text(d, (sc(cx), sc(HEADER_H / 2 + 44)), str(n), f, HEADER_TEXT, "ms")


def tile(img, g, x, y):
    ico = fit_cover(get_image(g["titleIconUrl"]), sc(TILE))
    mask = rounded_mask(ico.size, sc(RADIUS))
    img.paste(ico, (sc(x), sc(y)), mask)

    # bottom gradient for the date
    gh = sc(84)
    grad = Image.new("RGBA", (sc(TILE), gh), (0, 0, 0, 0))
    gp = grad.load()
    for yy in range(gh):
        a = int(225 * (yy / gh) ** 1.4)
        for xx in range(sc(TILE)):
            gp[xx, yy] = (8, 12, 20, a)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(grad, (sc(x), sc(y + TILE) - gh))
    clip = Image.new("L", img.size, 0)
    clip.paste(mask, (sc(x), sc(y)))
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", img.size, 0), clip))
    img.alpha_composite(layer)

    f_small = font(14, 700)
    # platform pill (top-left)
    plat = g.get("platform") or ""
    pw = f_small.getlength(plat) / S + 18
    pill(img, x + 10, y + 10, x + 10 + pw, y + 34)
    d = ImageDraw.Draw(img)
    draw_text(d, (sc(x + 10 + pw / 2), sc(y + 22)), plat, f_small, PLATFORM_COLOR, "mm")

    # rarity pill (top-right)
    r = f"{g['platRarityPercent']:.1f}%"
    rw = f_small.getlength(r) / S + 18
    pill(img, x + TILE - 10 - rw, y + 10, x + TILE - 10, y + 34)
    d = ImageDraw.Draw(img)
    draw_text(d, (sc(x + TILE - 10 - rw / 2), sc(y + 22)), r, f_small,
              RARITY_COLOR.get(g.get("platRarityType"), RARITY_COLOR[3]), "mm")

    # earned date (bottom, centred)
    earned = datetime.strptime(g["platEarnedDate"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    date = earned.astimezone(TZ).strftime("%b %d, %Y")
    draw_text(d, (sc(x + TILE / 2), sc(y + TILE - 12)), date, font(16, 600), (255, 255, 255), "ms")


def render(plats, prof):
    rows = -(-len(plats) // COLS)
    w = 2 * PAD + COLS * TILE + (COLS - 1) * GAP
    h = HEADER_H + PAD + rows * TILE + (rows - 1) * GAP + PAD
    img = Image.new("RGBA", (sc(w), sc(h)), PAGE_BG + (255,))
    header(img, prof)
    for i, g in enumerate(plats):
        r, c = divmod(i, COLS)
        tile(img, g, PAD + c * (TILE + GAP), HEADER_H + PAD + r * (TILE + GAP))
    return img.convert("RGB")


def main():
    plats, prof = load_data()
    sig = signature(plats, prof)
    if os.path.exists(HASH_FILE) and open(HASH_FILE).read().strip() == sig:
        print("No meaningful change -> skip render, no commit.")
        return
    print(f"Change detected -> rendering {len(plats)} platinums...")
    img = render(plats, prof)
    os.makedirs("assets", exist_ok=True)
    img.save(OUT, optimize=True)
    with open(HASH_FILE, "w") as f:
        f.write(sig)
    print(f"Wrote {OUT} ({img.width}x{img.height}) + {HASH_FILE}")


if __name__ == "__main__":
    sys.exit(main())
