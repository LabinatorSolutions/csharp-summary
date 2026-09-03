#!/usr/bin/env python3
"""
Render the TechWorld with Milan one-page C# cheat sheet to PNG (2x) and PDF,
then run a QA pass over the live DOM.

    python3 one-page/render.py

QA checks (any FAIL must be fixed before shipping):
  - horizontal overflow of the page and of every code block
  - content cut off at the bottom of the canvas
  - any text rendered below 13px
  - box-shadow anywhere (brand rule)
  - emoji anywhere (brand rule)
  - per-column heights (must be within BALANCE_TOLERANCE of each other)
  - page height inside TARGET_MIN..TARGET_MAX
"""

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
HTML = HERE / "csharp-cheatsheet.html"
PNG = HERE / "csharp-cheatsheet.png"
PDF = HERE / "csharp-cheatsheet.pdf"

WIDTH = 1600
SCALE = 2
TARGET_MIN = 2100
TARGET_MAX = 2300
BALANCE_TOLERANCE = 60
MIN_FONT_PX = 13.0

EMOJI = re.compile(
    "[" "\U0001F000-\U0001FAFF" "←-⇿" "⌀-⏿"
    "①-⓿" "■-➿" "⬀-⯿" "️" "]"
)

JS_AUDIT = r"""
() => {
  const out = {
    overflowX: [], smallFont: [], shadows: [], emoji: [], columns: [],
    docWidth: document.documentElement.scrollWidth,
    docHeight: document.documentElement.scrollHeight,
  };
  const canvas = document.querySelector('.canvas');
  const cRect = canvas.getBoundingClientRect();
  out.canvasHeight = cRect.height;

  const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2190}-\u{21FF}\u{2300}-\u{23FF}\u{2460}-\u{24FF}\u{25A0}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/u;

  const label = el => {
    const p = el.closest('section.p');
    const head = p ? p.querySelector('h2').textContent.trim() : '(page)';
    return head + ' :: ' + el.tagName.toLowerCase() + ' "' +
           (el.textContent || '').trim().slice(0, 46).replace(/\s+/g, ' ') + '"';
  };

  document.querySelectorAll('*').forEach(el => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();

    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      out.overflowX.push({ where: label(el), scroll: el.scrollWidth, client: el.clientWidth });
    }
    if (r.right > cRect.right + 0.5) {
      out.overflowX.push({ where: label(el), scroll: Math.round(r.right), client: Math.round(cRect.right) });
    }
    if (cs.boxShadow && cs.boxShadow !== 'none') {
      out.shadows.push(label(el));
    }
    // Only leaf-ish text nodes matter for font size
    const hasText = Array.from(el.childNodes).some(
      n => n.nodeType === 3 && n.textContent.trim().length);
    if (hasText) {
      const fs = parseFloat(cs.fontSize);
      if (fs < %MINFONT%) out.smallFont.push({ where: label(el), px: fs });
      const t = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3).map(n => n.textContent).join('');
      if (EMOJI.test(t)) out.emoji.push(label(el));
    }
  });

  document.querySelectorAll('.cols > .col').forEach((col, i) => {
    const cr = col.getBoundingClientRect();
    const panels = Array.from(col.querySelectorAll(':scope > section.p')).map(p => ({
      title: p.querySelector('h2').textContent.trim().replace(/\s+/g, ' '),
      h: Math.round(p.getBoundingClientRect().height),
    }));
    out.columns.push({ index: i + 1, height: Math.round(cr.height), panels });
  });

  // deepest painted bottom edge inside the canvas
  let lowest = 0;
  document.querySelectorAll('.canvas *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.height > 0 && r.bottom > lowest) lowest = r.bottom;
  });
  out.lowestContent = Math.round(lowest);
  return out;
}
""".replace("%MINFONT%", str(MIN_FONT_PX))


def find_chromium():
    """Preinstalled Chromium may not match the pip playwright build number.
    Fall back to whatever is actually on disk (never run `playwright install`)."""
    import os
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if not root.is_dir():
        return None
    for pattern in ("chromium_headless_shell-*/chrome-linux/chrome-headless-shell",
                    "chromium-*/chrome-linux/chrome"):
        hits = sorted(root.glob(pattern))
        if hits:
            return str(hits[-1])
    return None


def main() -> int:
    failures, warnings = [], []

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception:
            exe = find_chromium()
            if not exe:
                raise
            print(f"(using preinstalled chromium: {exe})")
            browser = pw.chromium.launch(executable_path=exe)
        page = browser.new_page(viewport={"width": WIDTH, "height": 1200},
                                device_scale_factor=SCALE)
        page.goto(HTML.as_uri())
        page.wait_for_timeout(400)
        try:
            page.evaluate("document.fonts.ready")
        except Exception:
            pass
        page.wait_for_timeout(300)

        height = page.evaluate(
            "() => Math.ceil(document.querySelector('.canvas').getBoundingClientRect().height)")
        page.set_viewport_size({"width": WIDTH, "height": height})
        page.wait_for_timeout(200)

        audit = page.evaluate(JS_AUDIT)

        page.locator(".canvas").screenshot(path=str(PNG))
        page.pdf(path=str(PDF), width=f"{WIDTH}px", height=f"{height}px",
                 print_background=True, margin={"top": "0", "bottom": "0",
                                                "left": "0", "right": "0"},
                 prefer_css_page_size=False)
        browser.close()

    # ---------------- report ----------------
    print("=" * 74)
    print(f"canvas            : {WIDTH} x {height} px   (PNG at {SCALE}x = "
          f"{WIDTH * SCALE} x {height * SCALE})")
    print(f"document scrollW  : {audit['docWidth']}")
    print(f"lowest content px : {audit['lowestContent']}  (canvas "
          f"{round(audit['canvasHeight'])})")
    print("=" * 74)

    heights = []
    for col in audit["columns"]:
        heights.append(col["height"])
        print(f"\ncolumn {col['index']}  height {col['height']}px")
        for p in col["panels"]:
            print(f"    {p['h']:>5}px  {p['title']}")
    spread = max(heights) - min(heights)
    print(f"\ncolumn heights    : {heights}   spread {spread}px "
          f"(tolerance {BALANCE_TOLERANCE})")
    print("=" * 74)

    if audit["docWidth"] > WIDTH:
        failures.append(f"horizontal overflow: document scrollWidth "
                        f"{audit['docWidth']} > {WIDTH}")
    for o in audit["overflowX"]:
        failures.append(f"overflow-x  {o['where']}  ({o['scroll']} > {o['client']})")
    if audit["lowestContent"] > audit["canvasHeight"] + 1:
        failures.append(f"content cut off at the bottom: lowest "
                        f"{audit['lowestContent']} > canvas "
                        f"{round(audit['canvasHeight'])}")
    for s in audit["smallFont"]:
        failures.append(f"font {s['px']}px < {MIN_FONT_PX}px  {s['where']}")
    for s in audit["shadows"]:
        failures.append(f"box-shadow  {s}")
    for e in audit["emoji"]:
        failures.append(f"emoji  {e}")
    if spread > BALANCE_TOLERANCE:
        failures.append(f"columns unbalanced: spread {spread}px > "
                        f"{BALANCE_TOLERANCE}px")
    if not (TARGET_MIN <= height <= TARGET_MAX):
        failures.append(f"page height {height}px outside target "
                        f"{TARGET_MIN}-{TARGET_MAX}px")

    src = HTML.read_text(encoding="utf-8")
    if EMOJI.search(src):
        failures.append("emoji found in the HTML source")
    if re.search(r"box-shadow\s*:", src):
        failures.append("box-shadow found in the HTML source")

    for w in warnings:
        print(f"WARN  {w}")
    if failures:
        print(f"\n{len(failures)} FAIL:")
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("\nPASS  all checks clean")
    print(f"  {PNG}")
    print(f"  {PDF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
