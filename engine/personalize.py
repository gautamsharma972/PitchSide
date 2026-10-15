"""Personalization: one neutral intelligence stream -> a different experience per viewer.

Profile: mode (analyst|fan), lang, favourite club, favourite players, focus player (follow one player through the
match) and a single metric ticker. Output is machine-readable overlay JSON a graphics renderer can draw directly."""
from typing import Dict, List, Optional

from . import i18n

MODES = ("analyst", "fan")
METRICS = ("none", "speed", "distance", "xg", "shots", "tackles", "passes", "pass_accuracy", "possession")
FAN_OVERLAYS = {"player_tag", "lower_third", "shot_card"}


def parse_profile(lang="en", mode="analyst", team=None, players="", focus=None, metric="none") -> Dict:
    return {"lang": lang if lang in i18n.LANGS else "en", "mode": mode if mode in MODES else "analyst",
            "team": team if team in ("home", "away") else None,
            "players": [p.strip() for p in (players or "").split(",") if p.strip()][:5],
            "focus": focus or None, "metric": metric if metric in METRICS else "none"}


def _text(sig: Dict, prof: Dict) -> str:
    return i18n.render(sig["key"], sig["params"], prof["lang"], prof["mode"])


def _mine(sig: Dict, prof: Dict) -> Optional[str]:
    t = prof["team"]
    if not t or not sig.get("team"):
        return None
    return "for" if sig["team"] == t else "against"


def render(frame: Dict, prof: Dict, engine, teams: Dict) -> Dict:
    """Return the viewer-specific frame. `engine` supplies live player/metric views."""
    e, fan = frame["event"], prof["mode"] == "fan"
    focus, favs = prof["focus"], set(prof["players"])
    lang = prof["lang"]
    involved = {e.get("player"), e.get("to_player")} - {"", None}
    feed: List[Dict] = []
    for s in frame["signals"]:
        side = _mine(s, prof)
        pr = s["priority"] + (1 if side == "for" else 0)
        is_fav = bool(favs & {s.get("player"), *(s["params"].get(k) for k in ("player",))})
        if focus and focus not in (s.get("player"), s["params"].get("player")) and s["priority"] < 5:
            continue                                        # player-focus mode: only their moments (plus goals/reds)
        if fan and not is_fav and pr < 3 and not (focus and focus == s.get("player")):
            continue
        if fan and side == "against" and s["priority"] < 4:
            continue
        txt = _text(s, prof)
        if not txt:
            continue
        if side == "for": txt = i18n.render("mine_for", {}, lang) + txt
        elif side == "against" and s["kind"] == "story": txt = i18n.render("mine_against", {}, lang) + txt
        if is_fav and s["kind"] != "insight": txt = i18n.render("fav_player", {"player": s.get("player") or ""}, lang) + txt
        feed.append({"id": s["id"], "kind": s["kind"], "key": s["key"], "priority": min(5, pr), "text": txt,
                     "team": s.get("team"), "player": s.get("player"), "mine": side, "fav": is_fav,
                     "evidence": None if fan else s.get("evidence")})

    sig_by_id = {s["id"]: s for s in frame["signals"]}
    overlays = []
    for o in frame["overlays"]:
        p = o["payload"]
        t = o["type"]
        if focus and not (involved & {focus}) and t != "lower_third":
            continue
        if focus and t == "player_tag" and p["player"] != focus:
            continue
        if fan:
            if t not in FAN_OVERLAYS:
                continue
            if t == "player_tag" and not (p.get("indicator") or p.get("event") in ("goal", "red_card", "yellow_card", "shot_saved")
                                          or p["player"] in favs or p["player"] == focus):
                continue
        if t == "lower_third":
            sid = o["id"].rsplit("-", 1)[0]
            sig = sig_by_id.get(sid)
            if not sig:
                continue
            side = _mine(sig, prof)
            if fan and side == "against" and sig["priority"] < 4:
                continue
            txt = _text(sig, prof)
            if not txt:
                continue
            overlays.append(dict(o, payload=dict(p, text=txt, lang=lang)))
            continue
        q = dict(p)
        if fan:                                              # casual view: no raw numbers except speed + xG headline
            if t == "player_tag":
                q.pop("distance_km", None)
            if t == "shot_card":
                q.pop("distance_m", None)
        if p.get("player") in favs:
            q["fav"] = True
        overlays.append(dict(o, payload=q))

    out = {"seq": frame["seq"], "event": e, "state": frame["state"], "feed": feed, "overlays": overlays,
           "latency_ms": frame["latency_ms"], "profile": {k: prof[k] for k in ("lang", "mode", "team", "focus", "metric")}}
    if focus:
        out["player_card"] = engine.player_card(focus)
    elif favs and involved & favs:
        out["player_card"] = engine.player_card(next(iter(involved & favs)))
    out["ticker"] = ticker(engine, prof)
    return out


def ticker(engine, prof: Dict) -> Optional[Dict]:
    m = prof["metric"]
    snap = engine.last_frame["state"] if engine.last_frame else engine.snapshot()
    if m in ("speed", "distance", "xg", "shots", "tackles", "passes"):
        return {"metric": m, "leaders": engine.leaders(m, 3)}
    if m == "pass_accuracy":
        return {"metric": m, "home": snap["pass_acc"]["home"], "away": snap["pass_acc"]["away"], "unit": "%"}
    if m == "possession":
        return {"metric": m, "home": snap["poss"], "away": round(100 - snap["poss"], 1), "unit": "%"}
    return None
