#!/usr/bin/env python3
"""Capture the psnplathub platinum mosaic for the profile README.

Runs daily in CI, but only re-renders + rewrites files when the *meaningful*
trophy data (or the settings below) actually change -- so a no-op day produces
no commit. Rarity % is deliberately excluded from the signature so its daily
drift does not trigger commits.

The image is captured with Playwright's own screenshot of the mosaic element
rather than the site's "Download Mosaic" button. That button goes through
html-to-image, which rasterises the DOM via an SVG foreignObject and gets glyph
advances wrong -- text came out with inflated letter-spacing ("G r a n d") and
wrapped to extra lines. A native screenshot is what the browser actually shows.
"""
import hashlib
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

PSN_ID = "firefox15499"
OUT = "assets/psn-mosaic.png"
HASH_FILE = "assets/.mosaic-hash"

# Device pixel ratio for the capture; 2 keeps the output ~1328px wide.
SCALE = 2
DROP_WATERMARK = True

# psnplathub client-side settings (injected into localStorage before render).
OVERRIDES = {
    "headerBackgroundColor": "#0070cc",   # profile header background
    "showPlatEarnedDate": True,           # show platinum earned date
    "isAutoLayout": True,                 # auto column count
    "showPlatCount": False,               # hide the "#N" platinum index numbers
    "platEarnedDateYearOnly": False,      # full date, e.g. "Aug 13, 2025" (not just year)
    "isPlatEarnedDateTextBold": False,    # regular weight, not bold
    "showGameTitle": True,                # game title under each icon
    "showFullGameTitle": True,            # full title, never truncated
    "isGameTitleTextBold": True,          # site default: bold game title
    # text sizes -- all pinned to the site defaults so they can't drift
    "headerUsernameSize": 18,             # px
    "headerTrophyNumberSize": 14,         # px
    "gameTitleTextSize": 4,               # site scale step, not px
    "platformTextSize": 3,
    "platRarityTextSize": 3,
    "platEarnedDateTextSize": 3,
    # header text legibility on the blue background (delete these 3 for black):
    "headerTextColor": "#ffffff",
    "headerUsernameColor": "#ffffff",
    "headerLevelColor": "#ffffff",
}

# psnplathub has no bold toggle for the profile header -- username/level/trophy
# counts are hardcoded font-semibold/font-medium -- so override it in the DOM.
HEADER_FONT_WEIGHT = "400"
HEADER_CSS = f".psn-header-nobold, .psn-header-nobold * {{ font-weight: {HEADER_FONT_WEIGHT} !important; }}"

# psnplathub self-hosts Geist but its --font-geist-sans var resolves to an empty
# string, so text falls back to the runner's system font (DejaVu Sans on Linux).
# Pin the family to the site's intended font and wait for it before capturing.
#
# Deliberately load Geist from Google Fonts rather than using the site's own copy.
# The site ships a single variable face, and Chromium renders it ~10% wider at bold
# weight -- "Grand Theft Auto V" measured 125px against a 120px tile, so titles came
# out loosely tracked and wrapped an extra line. Google's build has real per-weight
# instances: the same string measures 113px and sits on one line.
FONT_FAMILY = "Geist"
FONT_URL = ("https://fonts.googleapis.com/css2?family="
            f"{FONT_FAMILY.replace(' ', '+')}:wght@400;500;600;700&display=block")
FONT_CSS = f'body, body * {{ font-family: "{FONT_FAMILY}", sans-serif !important; }}'
FONT_LINK_JS = """
async (url) => {
    const l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = url;
    // appended last, so these @font-face rules win for the family name
    const done = new Promise(r => { l.onload = () => r(true); l.onerror = () => r(false); });
    document.head.appendChild(l);
    return await done;
}
"""

# Tag the header, find the mosaic element, and optionally drop the watermark.
PREPARE_JS = """
(dropWatermark) => {
    // The profile header is the block wrapping the "Level N" label.
    const level = Array.from(document.querySelectorAll('span'))
        .find(s => /^Level\\s/.test((s.textContent || '').trim()));
    const header = level && level.closest('div.w-full.flex.flex-col');
    if (!header) return {ok: false, why: 'profile header not found'};
    header.classList.add('psn-header-nobold');

    const wrapper = header.closest('div.mb-4');
    const node = wrapper && wrapper.parentElement;
    if (!node) return {ok: false, why: 'mosaic element not found'};
    node.setAttribute('data-psn-capture', '1');

    let watermark = 0;
    if (dropWatermark) {
        Array.from(node.querySelectorAll('span')).forEach(s => {
            if (/^psnplathub\\.com$/i.test((s.textContent || '').trim())) {
                (s.parentElement || s).style.display = 'none';
                watermark++;
            }
        });
    }

    // Icons are lazy-loaded; force them all in so nothing captures blank.
    document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
    return {ok: true, watermark, tiles: node.querySelectorAll('p[title]').length};
}
"""

FONT_LOAD_JS = """
async (family) => {
    // Load every weight the mosaic uses, for the glyphs actually on the page, so
    // all unicode-range subsets are fetched before the capture.
    const sample = document.body.innerText.slice(0, 20000);
    await Promise.all(['400', '500', '600', '700'].map(
        w => document.fonts.load(`${w} 16px "${family}"`, sample).catch(() => null)));
    await document.fonts.ready;
    return document.fonts.check(`400 16px "${family}"`);
}
"""

IMAGES_READY_JS = """
() => Array.from(document.querySelectorAll('[data-psn-capture] img'))
        .every(i => i.complete && i.naturalWidth > 0)
"""


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "psn-mosaic-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def signature():
    plats = get(f"https://psnplathub.com/api/plats?psnId={PSN_ID}")["plattedGames"]
    prof = get(f"https://psnplathub.com/api/profile?psnId={PSN_ID}")["profile"]
    payload = {
        "plats": sorted(
            (g["npCommunicationId"], g.get("platEarnedDate"), g.get("platform"))
            for g in plats
        ),
        "counts": prof.get("trophyCounts"),
        "level": prof.get("trophyLevel"),
        "settings": OVERRIDES,
        "headerFontWeight": HEADER_FONT_WEIGHT,
        "fontFamily": FONT_FAMILY,
        "fontUrl": FONT_URL,
        "scale": SCALE,
        "dropWatermark": DROP_WATERMARK,
        "capture": "element-screenshot",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def main():
    sig = signature()
    if os.path.exists(HASH_FILE) and open(HASH_FILE).read().strip() == sig:
        print("No meaningful change -> skip render, no commit.")
        return

    print("Change detected -> rendering mosaic...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # CI: Playwright's bundled Chromium
        page = browser.new_context(
            viewport={"width": 1600, "height": 2000}, device_scale_factor=SCALE
        ).new_page()
        page.set_default_timeout(90000)
        page.goto(f"https://psnplathub.com/mosaic?psnId={PSN_ID}", wait_until="networkidle")
        page.wait_for_timeout(4000)
        page.evaluate(
            """(ov) => {
                const c = JSON.parse(localStorage.getItem('mosaicSettings') || '{}');
                localStorage.setItem('mosaicSettings', JSON.stringify(Object.assign(c, ov)));
            }""",
            OVERRIDES,
        )
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(6000)

        if not page.evaluate(FONT_LINK_JS, FONT_URL):
            raise SystemExit(f"could not load {FONT_FAMILY} from {FONT_URL}")
        page.add_style_tag(content=FONT_CSS + HEADER_CSS)
        prep = page.evaluate(PREPARE_JS, DROP_WATERMARK)
        if not prep["ok"]:
            raise SystemExit(f"{prep['why']} (site may have changed)")
        if DROP_WATERMARK and not prep["watermark"]:
            raise SystemExit("watermark element not found (site may have changed)")
        if not page.evaluate(FONT_LOAD_JS, FONT_FAMILY):
            raise SystemExit(f"{FONT_FAMILY} webfont did not load (would fall back to a system font)")

        page.wait_for_function(IMAGES_READY_JS, timeout=60000)
        page.wait_for_timeout(1500)  # let the reflow from the webfont metrics settle
        print(f"  {prep['tiles']} tiles, watermark removed: {bool(prep['watermark'])}")

        os.makedirs("assets", exist_ok=True)
        page.locator("[data-psn-capture]").screenshot(path=OUT)
        browser.close()

    with open(HASH_FILE, "w") as f:
        f.write(sig)
    print(f"Wrote {OUT} + {HASH_FILE}")


if __name__ == "__main__":
    sys.exit(main())
