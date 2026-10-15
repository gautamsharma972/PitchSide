"""Record-proofing: drives the ?demo=1 walkthrough in a real (headless) Chromium-family browser, asserts every
step produced its visible result, fails on any console error / failed request, and saves a screenshot per step.

  .venv/bin/python scripts/demo_check.py [--url http://localhost:8000] [--browser /path/to/chrome-or-edge] [--out shots]
"""
import argparse
import os
import re
import sys
import json
import time
import urllib.request

from playwright.sync_api import sync_playwright

BROWSERS = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]
CJK = re.compile(r"[぀-ヿ一-鿿]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--browser", default=next((b for b in BROWSERS if os.path.exists(b)), None))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "export", "demo_shots"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    problems, results = [], []
    M = json.load(urllib.request.urlopen(a.url + "/api/match"))
    star = sorted(M["players"], key=lambda c: (-c["goals"], -c["xg"]))[0]["name"]
    final = "%d–%d" % (M["final"]["score"]["home"], M["final"]["score"]["away"])

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.browser, headless=True, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.on("console", lambda m: problems.append("console %s: %s" % (m.type, m.text)) if m.type == "error" else None)
        pg.on("pageerror", lambda e: problems.append("pageerror: %s" % e))
        pg.on("requestfailed", lambda r: problems.append("request failed: %s" % r.url) if "/api/live" not in r.url else None)
        pg.on("response", lambda r: problems.append("HTTP %d %s" % (r.status, r.url)) if r.status >= 400 else None)
        pg.goto(a.url + "/?demo=1", wait_until="domcontentloaded")
        pg.wait_for_selector(".moment", timeout=15000)   # dashboard data loaded

        def check(name, cond, detail=""):
            results.append((name, bool(cond), detail))
            print(("PASS " if cond else "FAIL ") + name + (" - " + str(detail) if detail else ""))

        def step(n, wait_s, test):
            pg.keyboard.press("ArrowRight") if n else pg.click("#d0")
            time.sleep(wait_s)
            pg.screenshot(path=os.path.join(a.out, "step%d.png" % (n + 1)))
            test()

        feed = lambda: pg.inner_text("#feed")
        step(0, 8, lambda: (check("1 live frames flowing", "events" in pg.inner_text("#health").lower() and
                                  int(re.search(r"(\d+) events", pg.inner_text("#health"), re.I).group(1)) > 50, pg.inner_text("#health")),
                            check("1 overlays drawn on pitch", pg.locator("#ov > *").count() >= 1 or pg.locator("#shots circle").count() >= 1),
                            check("1 latency budget (<2 ms/event)", float(re.search(r"([\d.]+) ms", pg.inner_text("#health"), re.I).group(1)) < 2)))
        step(1, 6, lambda: check("2 control panel populated", "possession" in pg.inner_text("#ctrl")))
        step(2, 7, lambda: check("3 fan + Japanese feed", CJK.search(feed()) is not None or pg.inner_text("#mode-lbl").lower().startswith("fan"), pg.inner_text("#mode-lbl")))
        step(3, 8, lambda: check("4 favourite ⭐ or Spanish text", "⭐" in feed() or "Tu equipo" in feed() or star in pg.inner_text("#pcardbox") or pg.locator("#fav").input_value() == star))
        step(4, 8, lambda: check("5 player card for focus", star in pg.inner_text("#pcardbox"), pg.inner_text("#pcardbox")[:80]))
        step(5, 4, lambda: check("6 metric ticker", "speed" in pg.inner_text("#ticker").lower(), pg.inner_text("#ticker")[:80]))
        step(6, 5, lambda: (check("7 narration shown", len(pg.inner_text("#story")) > 30, pg.inner_text("#story")[:70]),
                            check("7 grounded check", "grounded" in pg.inner_text("#check")),
                            check("7 verifier caught the hallucination", "REVISE" in pg.inner_text("#trace") and "APPROVED" in pg.inner_text("#trace"), pg.inner_text("#trace")[:120])))
        step(7, 4, lambda: check("8 agent answered", "km/h" in pg.inner_text("#chat"), pg.inner_text("#chat")[-70:]))
        step(8, 3, lambda: check("9 auto-event result", "recall" in pg.inner_text("#out")))
        # wait for the match to finish, then verify the end state
        pg.wait_for_selector("text=full time", timeout=90000)
        pg.screenshot(path=os.path.join(a.out, "final.png"))
        check("match reaches full time", True)
        check("final score " + final, final in pg.inner_text("#bs"), pg.inner_text("#bs"))
        b.close()

    for pr in problems:
        print("PROBLEM " + pr)
    bad = [r for r in results if not r[1]]
    print("\n%d/%d checks passed, %d browser problems. Screenshots: %s" % (len(results) - len(bad), len(results), len(problems), os.path.abspath(a.out)))
    sys.exit(1 if bad or problems else 0)


if __name__ == "__main__":
    main()
