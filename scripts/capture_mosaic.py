#!/usr/bin/env python3
"""Capture the psnplathub platinum mosaic for the profile README.

Runs daily in CI, but only re-renders + rewrites files when the *meaningful*
trophy data (or the settings below) actually change -- so a no-op day produces
no commit. Rarity % is deliberately excluded from the signature so its daily
drift does not trigger commits.
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
# counts are hardcoded font-semibold/font-medium -- so override it in the DOM
# before the export. html-to-image reads computed styles, so this lands in the PNG.
HEADER_FONT_WEIGHT = "400"
HEADER_CSS = f".psn-header-nobold, .psn-header-nobold * {{ font-weight: {HEADER_FONT_WEIGHT} !important; }}"
HEADER_TAG_JS = """
() => {
    // The profile header is the block wrapping the "Level N" label.
    const level = Array.from(document.querySelectorAll('span'))
        .find(s => /^Level\\s/.test((s.textContent || '').trim()));
    const header = level && level.closest('div.w-full.flex.flex-col');
    if (!header) return false;
    header.classList.add('psn-header-nobold');
    return true;
}
"""

# psnplathub self-hosts Geist but its --font-geist-sans var resolves to an empty
# string, so text falls back to the runner's system font (DejaVu Sans on Linux) and
# the PNG looks nothing like the site in a desktop browser. Pin the family to the
# site's intended font and wait for the webfont before exporting -- the @font-face
# rules are same-origin, so html-to-image can embed them.
FONT_FAMILY = "Geist"
FONT_CSS = f'body, body * {{ font-family: "{FONT_FAMILY}", sans-serif !important; }}'
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
            accept_downloads=True, viewport={"width": 1600, "height": 1800}
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

        page.add_style_tag(content=FONT_CSS + HEADER_CSS)
        if not page.evaluate(HEADER_TAG_JS):
            raise SystemExit("Profile header not found (site may have changed)")
        if not page.evaluate(FONT_LOAD_JS, FONT_FAMILY):
            raise SystemExit(f"{FONT_FAMILY} webfont did not load (would fall back to a system font)")
        page.wait_for_timeout(1500)  # let the reflow from the new metrics settle

        btns = page.get_by_role("button")
        idx = next(
            (i for i in range(btns.count())
             if "download" in (btns.nth(i).inner_text() or "").lower()),
            None,
        )
        if idx is None:
            raise SystemExit("Download Mosaic button not found (site may have changed)")

        os.makedirs("assets", exist_ok=True)
        with page.expect_download(timeout=60000) as dl:
            btns.nth(idx).click()
        dl.value.save_as(OUT)
        browser.close()

    with open(HASH_FILE, "w") as f:
        f.write(sig)
    print(f"Wrote {OUT} + {HASH_FILE}")


if __name__ == "__main__":
    sys.exit(main())
