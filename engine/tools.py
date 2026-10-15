"""Deterministic analytics tools exposed to the Foundry agent (and used by the offline fallback).
The agent never computes numbers itself: it calls these and cites event ids."""
import json
from typing import Dict

from . import key_moments, match_facts
from .i18n import render as i18n_render_raw


def i18n_render(s):
    return i18n_render_raw(s["key"], s["params"], "en")

TOOL_SPECS = [
    {"type": "function", "name": "get_match_state",
     "description": "Score, xG, shots, win probability and goals/cards up to a match minute (default full time).",
     "parameters": {"type": "object", "properties": {"minute": {"type": "integer", "description": "Match minute, 0-95"},
                                                     "event_id": {"type": "integer", "description": "State right after this event"}},
                    "required": [], "additionalProperties": False}, "strict": False},
    {"type": "function", "name": "get_key_moments",
     "description": "Most influential moments with numeric factors explaining why each mattered.",
     "parameters": {"type": "object", "properties": {"top": {"type": "integer"}},
                    "required": [], "additionalProperties": False}, "strict": False},
    {"type": "function", "name": "get_events",
     "description": "Raw events filtered by team (home/away), player name substring, or event type.",
     "parameters": {"type": "object", "properties": {"team": {"type": "string"}, "player": {"type": "string"},
                                                     "type": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": [], "additionalProperties": False}, "strict": False},
    {"type": "function", "name": "get_player_stats",
     "description": "Player performance card: passes, accuracy, pass difficulty, shots, xG, tackles, distance km, top speed.",
     "parameters": {"type": "object", "properties": {"player": {"type": "string", "description": "Full or partial name"}},
                    "required": ["player"], "additionalProperties": False}, "strict": False},
    {"type": "function", "name": "get_leaders",
     "description": "Top players for a metric: speed, distance, xg, shots, tackles, passes.",
     "parameters": {"type": "object", "properties": {"metric": {"type": "string"}},
                    "required": ["metric"], "additionalProperties": False}, "strict": False},
    {"type": "function", "name": "get_insights",
     "description": "Explainability signals: control vs chaos phases, pressing, momentum flips and milestones with the numbers behind them.",
     "parameters": {"type": "object", "properties": {"kind": {"type": "string", "description": "insight | milestone | story"}},
                    "required": [], "additionalProperties": False}, "strict": False},
]


def run_tool(match: Dict, name: str, args: Dict) -> Dict:
    if name == "get_match_state":
        minute, eid = args.get("minute"), args.get("event_id")
        evs = [e for e in match["events"] if (minute is None or e["minute"] <= minute) and (eid is None or e["id"] <= eid)]
        return match_facts(match, evs[-1]["id"]) if evs else {}
    if name == "get_key_moments":
        return {"moments": key_moments(match, top=int(args.get("top") or 6))}
    if name == "get_events":
        out = []
        for e in match["events"]:
            if args.get("team") and e["team"] != args["team"]: continue
            if args.get("player") and args["player"].lower() not in e["player"].lower(): continue
            if args.get("type") and args["type"] not in e["type"]: continue
            out.append({k: e[k] for k in ("id", "minute", "team", "player", "type", "xg", "detail")})
        return {"events": out[: int(args.get("limit") or 15)]}
    if name == "get_player_stats":
        eng = match.get("engine")
        nm = next((n for n in (eng.players if eng else []) if (args.get("player") or "").lower() in n.lower()), None)
        return eng.player_card(nm) if nm else {"error": "player not found"}
    if name == "get_leaders":
        eng = match.get("engine")
        return {"metric": args.get("metric"), "leaders": eng.leaders(args.get("metric", "speed"), 3) if eng else []}
    if name == "get_insights":
        sig = [s for s in match.get("signals", []) if not args.get("kind") or s["kind"] == args["kind"]]
        return {"signals": [{"clock": s["clock"], "key": s["key"], "player": s.get("player"), "params": s["params"],
                             "evidence": s.get("evidence")} for s in sig if s["kind"] != "milestone" or s["priority"] >= 3][:15]}
    return {"error": "unknown tool %s" % name}


def offline_answer(match: Dict, question: str) -> str:
    """No-Azure fallback: route the question to a tool and render its result."""
    q = question.lower()
    eng = match.get("engine")
    if eng and any(w in q for w in ("fastest", "speed", "sprint")):
        r = eng.leaders("speed", 1)
        if r: return "Fastest: %s (%s) at %.1f km/h." % (r[0]["player"], match["teams"][r[0]["team"]]["name"], r[0]["value"])
    if eng and any(w in q for w in ("ran", "run ", "distance", "covered")):
        r = eng.leaders("distance", 1)
        if r: return "Most distance: %s with %.2f km." % (r[0]["player"], r[0]["value"])
    if eng:
        for n in eng.players:
            if n.lower() in q or n.split()[-1].lower() in q:
                c = eng.player_card(n)
                return "%s: %s passes (%s%% accuracy), %d shots, %.2f xG, %d goals, %.2f km, top speed %.1f km/h." % (
                    n, c["passes"], c["pass_acc"], c["shots"], c["xg"], c["goals"], c["distance_km"], c["top_speed"])
    if any(w in q for w in ("control", "chaos", "press", "rhythm", "tempo")):
        ins = [s for s in match.get("signals", []) if s["kind"] == "insight"][:3]
        if ins:
            return " ".join("%s: %s." % (s["clock"], i18n_render(s)) for s in ins)
    if any(w in q for w in ("why", "key", "moment", "turn", "swing")):
        ms = key_moments(match, top=3)
        return " ".join("%d' %s by %s [#%d]: %s." % (m["minute"], m["type"].replace("_", " "), m["player"] or "-",
                        m["event_id"], m["factors"][-1]["note"]) for m in ms)
    f = match_facts(match)
    return "%s %d-%d %s (xG %.2f-%.2f, shots %d-%d)." % (f["home"], f["score"]["home"], f["score"]["away"], f["away"],
                                                        f["xg"]["home"], f["xg"]["away"], f["shots"]["home"], f["shots"]["away"])
