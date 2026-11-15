#!/usr/bin/env python3
"""What breaks on a phone, per dashboard view, measured rather than eyeballed.

Loads each view at phone and tablet width in headless Chrome with touch
emulation on, and fails if any of these is true:

  * the document scrolls sideways (something is wider than the viewport);
  * a table hides columns inside its own scroller at phone width, which means
    a row that should have folded into cards did not;
  * a control that reads as a button is under TAP_MIN px tall.

Why a script and not a one-off check: most of a sideways scroll comes from a
grid or flex child keeping its automatic minimum size, so one wide child -- a
nowrap button, a table, a long CJK label -- silently pushes the whole page
out. That is a one-class fix and a class of bug that comes back the next time
someone adds a wide element, which is exactly what a test is for.

Run it against a server showing real data, since an empty dashboard has no
layout to test:

    cd apps/frontend
    python3 scripts/rwd-audit.py http://127.0.0.1:3412

Prerequisites: the `websockets` package, and a Chromium binary. Both are
already present for the repo's other browser checks; point CHROME at another
build if yours lives elsewhere. It drives Chrome over CDP rather than adding a
browser-automation dependency to the frontend's package.json.

Exit status is 0 when every view passes, 1 otherwise, so it can gate a deploy
next to scripts/check-build.sh.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websockets.sync.client as wsclient

CHROME = os.environ.get(
    "CHROME",
    "/home/ruby0322/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
)
PORT = int(os.environ.get("RWD_AUDIT_PORT", "9417"))
# zh-TW rather than en: CJK labels are about 1.5x the width of their English
# equivalents, so sizing against English is what caused the wrapped buttons
# this check exists to catch.
LOCALE = os.environ.get("RWD_AUDIT_LOCALE", "zh-TW")
SHOT_DIR = os.environ.get("RWD_AUDIT_SHOTS")

# Each view with a marker that only exists once its data has arrived. Waiting
# on readyState alone measures the loading state, which has no layout to test
# and reports a clean bill of health for a page that never rendered.
VIEWS = [
    ("", "dashboard-ongoing"),
    ("/applications", "application-flow"),
    ("/matches", "matches-weekly"),
    ("/resumes", "resumes-list"),
    ("/profile", "profile-preference"),
    ("/usage", "usage-allowance"),
]

# iPhone 13 mini / SE-era width, and a small tablet.
WIDTHS = [(390, "phone"), (768, "tablet")]

# A thumb needs about 40px. Checked only where the pointer is coarse, which is
# what the CSS keys on too -- a desktop keeps its dense tables.
TAP_MIN = 40

PROBE = """JSON.stringify((() => {
  const vw = document.documentElement.clientWidth;
  const wide = [];
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > vw + 1 || r.width > vw + 1) wide.push(el);
  }
  // Only the innermost ones are causes: every ancestor of a wide element is
  // wide too, so reporting all of them buries the one line to change.
  const set = new Set(wide);
  const causes = wide
    .filter((el) => ![...el.querySelectorAll('*')].some((k) => set.has(k)))
    .slice(0, 8)
    .map((el) => {
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 56),
        w: Math.round(r.width),
        right: Math.round(r.right),
        text: (el.textContent || '').trim().slice(0, 36),
      };
    });

  // A table that scrolls inside its own box still hides columns.
  const clipped = [];
  for (const w of document.querySelectorAll('main .overflow-x-auto')) {
    if (w.scrollWidth > w.clientWidth + 1) {
      clipped.push({
        hidden: Math.round(w.scrollWidth - w.clientWidth),
        of: Math.round(w.scrollWidth),
        text: (w.textContent || '').trim().slice(0, 28),
      });
    }
  }

  // Tap targets, which is not the same as every link: a word underlined in a
  // sentence is prose, and padding it to 40px would wreck the paragraph it
  // sits in. What has to be thumb-sized is what reads as a control.
  const small = [];
  const coarse = window.matchMedia('(pointer: coarse)').matches;
  for (const el of document.querySelectorAll('main a, main button')) {
    const cls = (el.className || '').toString();
    const cs = getComputedStyle(el);
    // Classified by how it looks, not by its tag. A <button> styled as an
    // underlined word in a sentence is prose and padding it to 40px wrecks the
    // paragraph; a chip with a border is a control whatever its tag. Border
    // *width*, not style: Tailwind's preflight sets `border-style: solid` on
    // every element, so testing the style counts every link on the page.
    const filled = cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent';
    const bordered = parseFloat(cs.borderTopWidth) > 0 || parseFloat(cs.borderBottomWidth) > 0;
    if (!/\\brr-btn\\b/.test(cls) && !filled && !bordered) continue;
    const r = el.getBoundingClientRect();
    if (r.height === 0) continue;
    if (r.height < TAP_MIN - 0.5) {
      small.push({
        tag: el.tagName.toLowerCase(),
        h: Math.round(r.height * 10) / 10,
        cls: cls.slice(0, 48),
        text: (el.textContent || '').trim().slice(0, 24),
      });
    }
  }

  return {
    ready: document.readyState,
    path: location.pathname,
    loaded: !!document.querySelector('[data-testid="MARKER"]'),
    vw,
    coarse,
    pageScrollsSideways: document.documentElement.scrollWidth > vw + 1,
    scrollWidth: document.documentElement.scrollWidth,
    causes,
    wideCount: wide.length,
    clipped,
    small: small.slice(0, 8),
    smallCount: small.length,
  };
})())"""


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3412"
    chrome = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures = 0
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json/version", timeout=2
                ) as r:
                    ws_url = json.load(r)["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.5)
        if not ws_url:
            print("could not reach Chrome's debugging port", file=sys.stderr)
            return 1
        ws = wsclient.connect(ws_url, max_size=90 * 1024 * 1024)
        seq = 0

        def send(method, params=None, session=None):
            nonlocal seq
            seq += 1
            msg = {"id": seq, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            ws.send(json.dumps(msg))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == seq:
                    return reply.get("result", {})

        target = send("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = send("Target.attachToTarget", {"targetId": target, "flatten": True})[
            "sessionId"
        ]
        send("Page.enable", session=sid)
        send("Runtime.enable", session=sid)

        for width, label in WIDTHS:
            print(f"\n=== {label} ({width}px) ===")
            send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": 844,
                    "deviceScaleFactor": 2,
                    "mobile": True,
                },
                session=sid,
            )
            # Without this the page still reports `pointer: fine`, so every
            # coarse-pointer rule is invisible to the measurement and the
            # targets look broken while the real phone is fine.
            send(
                "Emulation.setTouchEmulationEnabled",
                {"enabled": True, "maxTouchPoints": 5},
                session=sid,
            )
            for view, marker in VIEWS:
                path = f"/{LOCALE}/dashboard{view}"
                name = view.strip("/") or "overview"
                send("Page.navigate", {"url": base + path}, session=sid)
                info: dict = {}
                for _ in range(40):
                    time.sleep(0.5)
                    value = send(
                        "Runtime.evaluate",
                        {
                            "expression": PROBE.replace("MARKER", marker).replace(
                                "TAP_MIN", str(TAP_MIN)
                            ),
                            "returnByValue": True,
                        },
                        session=sid,
                    )["result"]["value"]
                    info = json.loads(value)
                    # Both guards matter: a stale execution context answers for
                    # the previous view, and readyState alone answers before the
                    # data lands.
                    if (
                        info.get("path") == path
                        and info.get("ready") == "complete"
                        and info.get("loaded")
                    ):
                        break
                if not info.get("loaded"):
                    print(f"  NODATA   {name} — never rendered, not measured")
                    failures += 1
                    continue
                if not info.get("coarse"):
                    print("  (pointer is not coarse; tap targets not meaningful here)")
                sideways = info.get("pageScrollsSideways")
                clipped = info.get("clipped", [])
                small = info.get("smallCount", 0)
                bad = bool(sideways) or small > 0 or (label == "phone" and clipped)
                failures += 1 if bad else 0
                print(
                    f"  {'FAIL' if bad else 'ok  '} {name:13s} "
                    f"scrollW={info.get('scrollWidth')} "
                    f"wide={info.get('wideCount')} "
                    f"clipped-tables={len(clipped)} "
                    f"small-targets={small}"
                )
                for c in info.get("causes", []):
                    print(
                        f"        > too wide: {c['tag']} w={c['w']} right={c['right']} "
                        f"{c['cls']!r} {c['text']!r}"
                    )
                for c in clipped:
                    print(f"        > table hides {c['hidden']}px of {c['of']}px")
                for s in info.get("small", []):
                    print(
                        f"        > {s['h']}px tall: {s['tag']} {s['text']!r} {s['cls']!r}"
                    )
                if SHOT_DIR and label == "phone":
                    shot = send(
                        "Page.captureScreenshot",
                        {"format": "png", "captureBeyondViewport": True},
                        session=sid,
                    )["data"]
                    out = os.path.join(SHOT_DIR, f"phone-{name}.png")
                    with open(out, "wb") as fh:
                        fh.write(base64.b64decode(shot))
        print("\nRESULT:", "every view passes" if not failures else f"{failures} failed")
        return 1 if failures else 0
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()


if __name__ == "__main__":
    sys.exit(main())
