"""Explainable analytics: win probability, key moments (with numeric 'why' factors) and grounding fact sheets.

Per-event statistics come from live.LiveEngine, so batch and live results are identical by construction."""
import math
from typing import Dict, List

TOTAL_MIN = 96.0
PRIOR_RATE = 1.3 / 90.0    # prior goals/min per team
PRIOR_WEIGHT = 25.0        # minutes of "pseudo data" behind the prior


def _poisson_pmf(lam: float, kmax: int = 8) -> List[float]:
    return [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(kmax + 1)]


def win_probability(score: Dict[str, int], xg: Dict[str, float], minute: float, reds: Dict[str, int]) -> Dict[str, float]:
    """P(home win / draw / away win): independent Poisson on remaining goals, scoring rate shrunk towards a prior."""
    remaining = max(0.0, TOTAL_MIN - minute)
    lam = {}
    for t, o in (("home", "away"), ("away", "home")):
        rate = (PRIOR_RATE * PRIOR_WEIGHT + xg[t]) / (PRIOR_WEIGHT + minute)
        rate *= 0.65 if reds[t] else 1.0
        rate *= 1.3 if reds[o] else 1.0
        lam[t] = rate * remaining
    ph, pa = _poisson_pmf(lam["home"]), _poisson_pmf(lam["away"])
    pw = pd = pl = 0.0
    for i, a in enumerate(ph):
        for j, b in enumerate(pa):
            diff = score["home"] + i - (score["away"] + j)
            p = a * b
            if diff > 0: pw += p
            elif diff == 0: pd += p
            else: pl += p
    tot = pw + pd + pl
    return {"home": pw / tot, "draw": pd / tot, "away": pl / tot}


def enrich(match: Dict) -> Dict:
    """Run the whole event list through the live engine (annotates e['state']) and keep its signals."""
    from .live import LiveEngine
    eng = LiveEngine(match["teams"], match.get("meta"))
    signals = []
    for e in match["events"]:
        signals.extend(eng.ingest(e)["signals"])
    match["engine"], match["signals"] = eng, signals
    return match


def _factors(e: Dict, prev: Dict) -> List[Dict]:
    """Human-readable, numeric reasons an event mattered."""
    f, s = [], e["state"]
    if e["type"] == "goal":
        f.append({"factor": "scoreline", "value": "%d-%d" % (s["score"]["home"], s["score"]["away"]), "note": "goal changes the score"})
        f.append({"factor": "shot_quality", "value": e["xg"], "note": "xG of the chance (%s)" % e["detail"]})
        if e["xg"] < 0.1:
            f.append({"factor": "against_the_odds", "value": e["xg"], "note": "low-probability finish"})
    if e["type"] == "shot_saved" and e["xg"] >= 0.25:
        f.append({"factor": "big_chance", "value": e["xg"], "note": "high-xG chance denied"})
    if e.get("shot_speed"):
        f.append({"factor": "shot_speed_kmh", "value": e["shot_speed"], "note": "ball speed off the boot"})
    if e["type"] == "red_card":
        f.append({"factor": "numerical_disadvantage", "value": 1, "note": "team plays a man down"})
    mom_shift = s["momentum"] - prev.get("momentum", 0.0)
    if abs(mom_shift) > 1.0:
        f.append({"factor": "momentum_shift", "value": round(mom_shift, 2),
                  "note": "recent threat balance moved towards %s" % ("home" if mom_shift > 0 else "away")})
    if s.get("phase") and s["phase"] != "balanced":
        f.append({"factor": "game_phase", "value": s["phase"], "note": "match context when it happened"})
    best = max(s["wp_swing"], key=lambda k: abs(s["wp_swing"][k]))
    f.append({"factor": "win_probability_swing", "value": round(s["wp_swing"][best] * 100, 1),
              "note": "%s outcome moved %+.1f pts" % (best, s["wp_swing"][best] * 100)})
    return f


def build_moment(evs: List[Dict], i: int) -> Dict:
    e = evs[i]
    prev = evs[i - 1]["state"] if i else {"momentum": 0.0}
    return {"event_id": e["id"], "minute": e["minute"], "clock": e.get("clock", "%d'" % e["minute"]), "type": e["type"],
            "team": e["team"], "player": e["player"],
            "impact": round(max(abs(v) for v in e["state"]["wp_swing"].values()), 3),
            "factors": _factors(e, prev), "score": e["state"]["score"], "wp": e["state"]["wp"]}


def key_moments(match: Dict, top: int = 8) -> List[Dict]:
    evs = match["events"]
    scored = []
    for i, e in enumerate(evs):
        if e["type"] not in ("goal", "red_card", "shot_saved", "shot_off_target", "shot_blocked"):
            continue
        impact = max(abs(v) for v in e["state"]["wp_swing"].values())
        if e["type"] in ("red_card", "goal"): impact += 0.15
        if impact > 0.02:
            scored.append((impact, i))
    scored.sort(reverse=True)
    return [build_moment(evs, i) for i in sorted(i for _, i in scored[:top])]


def match_facts(match: Dict, upto_id: int = None) -> Dict:
    """Compact fact sheet used to ground the LLM (and to verify its output)."""
    evs = [e for e in match["events"] if upto_id is None or e["id"] <= upto_id]
    last = evs[-1]["state"]
    goals = [{"id": e["id"], "minute": e["minute"], "team": e["team"], "player": e["player"], "xg": e["xg"],
              "detail": e["detail"]} for e in evs if e["type"] == "goal"]
    cards = [{"id": e["id"], "minute": e["minute"], "team": e["team"], "player": e["player"], "type": e["type"]}
             for e in evs if e["type"] in ("red_card", "yellow_card")]
    facts = {"home": match["teams"]["home"]["name"], "away": match["teams"]["away"]["name"], "score": last["score"],
             "xg": last["xg"], "shots": last["shots"], "goals": goals, "cards": cards, "win_probability": last["wp"],
             "minute": evs[-1]["minute"], "possession_home_pct": last.get("poss")}
    eng = match.get("engine")
    if eng and upto_id is None:   # full-match extras (final engine state)
        facts["pass_accuracy_pct"] = eng.snapshot()["pass_acc"]
        facts["top_speed_kmh"] = [{"player": r["player"], "value": r["value"]} for r in eng.leaders("speed", 1)]
        facts["distance_leaders_km"] = [{"player": r["player"], "value": r["value"]} for r in eng.leaders("distance", 2)]
        facts["insights"] = [{"clock": s["clock"], "key": s["key"], "params": s["params"]}
                             for s in match.get("signals", []) if s["kind"] == "insight"][:8]
    return facts
