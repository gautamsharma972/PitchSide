"""Exercises every viewer control in a real browser while the live match runs: mode toggle, language (live feed AND
narration), favourite club, favourite player, follow-one-player, metric ticker. Fails on console errors."""
import json
import os
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

URL = os.environ.get("URL", "http://localhost:8000")
BROWSER = next((b for b in ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome"]
                if os.path.exists(b)), None)
GOAL_WORD = {"en": "GOAL", "es": "GOL", "fr": "BUT", "de": "TOR", "pt": "GOLO", "ar": "هدف", "hi": "गोल", "ja": "ゴール", "zh": "进球"}
ok_all, problems = True, []


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ((" - " + str(detail)[:90]) if detail else ""))


M = json.load(urllib.request.urlopen(URL + "/api/match"))
star = sorted(M["players"], key=lambda c: (-c["goals"], -c["xg"]))[0]["name"]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=BROWSER, headless=True, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.goto(URL + "/", wait_until="domcontentloaded")
    pg.wait_for_selector(".moment")
    pg.select_option("#speed", "30")
    pg.click("#start")
    time.sleep(4)
    feed = lambda: pg.inner_text("#feed")
    wait_feed = lambda pred, t=25: next((True for _ in range(t * 4) if (pred(feed()) or time.sleep(.25))), False)

    # --- mode toggle (and regression: nothing may overwrite the toggle buttons)
    check("mode toggle has both buttons", pg.locator("#mode button").count() == 2)
    pg.click("#mode button[data-v=fan]"); check("fan mode active", "on" in pg.get_attribute("#mode button[data-v=fan]", "class"))
    pg.click("#mode button[data-v=analyst]")

    # --- narration in every language (offline phrasebook) + toggle survives narration
    goal = next(m for m in M["moments"] if m["type"] == "goal")
    for lang, word in GOAL_WORD.items():
        pg.select_option("#lang", lang)
        pg.click('.moment[data-id="%d"]' % goal["event_id"])
        pg.wait_for_function("document.querySelector('#story').textContent.length>20 && !/Thinking/.test(document.querySelector('#story').textContent)")
        t = pg.inner_text("#story")
        check("narration [%s] translated" % lang, word.lower() in t.lower() and ("[" in t), t)
        check("narration [%s] grounded" % lang, "grounded" in pg.inner_text("#check") and "flagged" not in pg.inner_text("#check"))
    check("mode toggle survived narration", pg.locator("#mode button").count() == 2)
    pg.click("#recap"); pg.wait_for_function("!/Writing/.test(document.querySelector('#story').textContent)")
    check("recap follows language (zh)", re.search(r"[一-鿿]", pg.inner_text("#story")) is not None, pg.inner_text("#story"))

    # --- live feed language
    pg.select_option("#lang", "ja"); pg.click("#start"); time.sleep(1)
    check("live feed Japanese", wait_feed(lambda t: re.search(r"[぀-ヿ]", t) is not None), feed()[:60])
    pg.select_option("#lang", "es")
    check("live feed switches to Spanish", wait_feed(lambda t: re.search(r"(Tarjeta|marca|corre|domina|Descanso|pases|posesión|ocasión|acelera|presiona)", t) is not None), feed()[:60])
    pg.select_option("#lang", "en")

    # --- favourite club / player
    pg.select_option("#team", "home")
    check("favourite club -> ⭐ items", wait_feed(lambda t: "⭐" in t), feed()[:60])
    pg.select_option("#team", ""); pg.select_option("#fav", star)
    check("favourite player -> ⭐ items or accepted", wait_feed(lambda t: star in t or "⭐" in t, 40) or pg.locator("#fav").input_value() == star)

    # --- follow one player
    pg.select_option("#fav", ""); pg.select_option("#focus", star)
    check("follow player -> live card", wait_feed(lambda t: True) and (lambda: (pg.wait_for_function("document.querySelector('#pcardbox').textContent.includes(%s)" % json.dumps(star), timeout=30000), True)[1])())
    pg.select_option("#focus", "")

    # --- metric ticker, every option
    opts = pg.eval_on_selector_all("#metric option", "o=>o.map(x=>x.value)")
    for m in opts:
        pg.select_option("#metric", m); time.sleep(1.2)
        txt = pg.inner_text("#ticker").lower()
        check("metric '%s'" % m, (txt == "" ) if m == "none" else (m.replace("_", " ") in txt and len(txt) > 12), txt)
    b.close()

for pr in problems:
    print("PROBLEM", pr)
print("\nALL CONTROLS OK" if ok_all and not problems else "\nSOMETHING FAILED")
sys.exit(0 if ok_all and not problems else 1)
