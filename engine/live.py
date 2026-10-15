"""Real-time match intelligence engine.

`LiveEngine.ingest(event)` is the heart of the pipeline and is incremental (O(1) per event, ~sub-millisecond):

  ingest -> interpret (stats, pass quality, speeds, distance, xG, momentum, win prob)
         -> explain   (control vs chaos, pressing, momentum flips - each with the numbers behind it)
         -> render    (language-neutral overlays + signals; personalize.py localises and filters per viewer)

The same code serves batch analysis (enrich) and the live hub, so what you see live is what you can audit later.
"""
import math
import time
from collections import deque
from typing import Dict, List

from .analytics import win_probability

ROLE_KMH = {"GK": 2.4, "CB": 4.9, "RB": 6.3, "LB": 6.3, "DM": 6.5, "CM": 7.0, "AM": 6.6, "RW": 6.8, "LW": 6.8, "ST": 6.2}
ROLES = ["GK", "RB", "CB", "CB", "LB", "DM", "CM", "AM", "RW", "ST", "LW"]
WINDOW_S = 600.0
DIST_MARKS = (3.0, 6.0, 9.0)
OVERLAY_EVENTS = {"pass", "shot_saved", "shot_blocked", "shot_off_target", "goal", "tackle", "interception",
                  "pressure", "sprint", "yellow_card", "red_card", "foul", "corner"}


def _other(t): return "away" if t == "home" else "home"


class LiveEngine:
    def __init__(self, teams: Dict, meta: Dict = None):
        self.teams, self.meta = teams, meta or {}
        self.seq = 0
        self.score = {"home": 0, "away": 0}
        self.xg = {"home": 0.0, "away": 0.0}
        self.reds = {"home": 0, "away": 0}
        self.mom = 0.0                      # >0 home pressure, <0 away pressure (decayed threat balance)
        self.last_el, self.last_poss = 0.0, None
        self.t = {k: dict(passes=0, passes_ok=0, shots=0, sot=0, tackles=0, tackles_won=0, interceptions=0,
                          pressures=0, turnovers=0, poss_s=0.0, corners=0, fouls=0, fwd_passes=0, final_third=0,
                          top_shot_kmh=0.0) for k in ("home", "away")}
        self.players: Dict[str, Dict] = {}
        for tk in ("home", "away"):
            for i, nm in enumerate(teams[tk]["players"]):
                role = ROLES[i]
                jitter = 0.93 + (sum(map(ord, nm)) % 15) / 100.0
                self.players[nm] = dict(name=nm, team=tk, role=role, passes=0, passes_ok=0, pass_dist=0.0,
                                        diff_sum=0.0, shots=0, sot=0, goals=0, xg=0.0, tackles_won=0, tackles=0,
                                        interceptions=0, pressures=0, sprints=0, top_speed=0.0, dist_m=0.0,
                                        rate=ROLE_KMH[role] * jitter, on=True, marks=set(), top_shot=0.0)
        self.win, self.win_tot = deque(), {}
        self.phase, self.phase_since = "balanced", -1e9
        self.press = {"home": False, "away": False}
        self.press_at = {"home": -1e9, "away": -1e9}
        self.mom_sign, self.mom_flip_at = 0, -1e9
        self.match_top_speed, self.match_top_shot = 0.0, 0.0
        self.wp_prev = win_probability(self.score, self.xg, 0.0, self.reds)
        self.last_eval = -1e9
        self.xg_marks = {"home": 0, "away": 0}
        self.last_frame = None

    # ------------------------------------------------------------------ window
    def _win_add(self, el, items):
        self.win.append((el, items))
        for k, v in items.items():
            self.win_tot[k] = self.win_tot.get(k, 0.0) + v
        while self.win and el - self.win[0][0] > WINDOW_S:
            _, old = self.win.popleft()
            for k, v in old.items():
                self.win_tot[k] -= v

    def _w(self, k): return self.win_tot.get(k, 0.0)

    def control(self, team):
        """Rolling (10 min) control metrics for `team`."""
        ph, pa = self._w("poss_home"), self._w("poss_away")
        tot = (ph + pa) or 1.0
        poss = (ph if team == "home" else pa) / tot
        p, ok = self._w("pass_" + team), self._w("pok_" + team)
        o = _other(team)
        defs = self._w("def_" + team)
        ppda = self._w("pass_" + o) / defs if defs else 99.0
        return {"poss": round(poss * 100, 1), "pass_acc": round(ok / p * 100, 1) if p else 0.0,
                "ppda": round(min(ppda, 99.0), 1), "def_actions": int(defs), "passes": int(p)}

    # ------------------------------------------------------------------ ingest
    def ingest(self, e: Dict) -> Dict:
        t0 = time.perf_counter()
        self.seq += 1
        el = float(e.get("elapsed", self.last_el))
        el = max(el, self.last_el)
        dt = el - self.last_el
        typ, team, name = e["type"], e["team"], e.get("player", "")
        e.setdefault("poss", team)
        signals: List[Dict] = []
        overlays: List[Dict] = []

        # time-based accumulators (possession, distance, momentum decay)
        items = {}
        if self.last_poss and dt:
            self.t[self.last_poss]["poss_s"] += dt
            items["poss_" + self.last_poss] = dt
        if dt:
            for p in self.players.values():
                if p["on"]:
                    p["dist_m"] += p["rate"] / 3.6 * dt
            self.mom *= 0.5 ** (dt / 360.0)
        self.last_poss, self.last_el = e["poss"], el

        pl = self.players.get(name)
        ts = self.t[team]
        succ = e.get("success")
        tag_extra = {}

        if typ == "pass":
            ts["passes"] += 1
            items["pass_" + team] = 1.0
            if pl: pl["passes"] += 1; pl["pass_dist"] += e.get("distance_m") or 0; pl["diff_sum"] += e.get("difficulty") or 0
            if succ:
                ts["passes_ok"] += 1
                items["pok_" + team] = 1.0
                if pl: pl["passes_ok"] += 1
                if (e.get("to_x") or 0) - (e.get("x") or 0) > 8: ts["fwd_passes"] += 1
                if (e.get("to_x") or 0) >= 70:
                    ts["final_third"] += 1
                    self._mom(team, 0.05)
        elif typ in ("shot_saved", "shot_blocked", "shot_off_target", "goal"):
            ts["shots"] += 1
            if typ in ("shot_saved", "goal"): ts["sot"] += 1
            xg = float(e.get("xg") or 0)
            self.xg[team] += xg
            self._mom(team, max(0.1, xg) * 4.0)
            spd = e.get("shot_speed") or 0
            ts["top_shot_kmh"] = max(ts["top_shot_kmh"], spd)
            if pl:
                pl["shots"] += 1; pl["xg"] += xg; pl["top_shot"] = max(pl["top_shot"], spd)
                if typ in ("shot_saved", "goal"): pl["sot"] += 1
            if typ == "goal":
                self.score[team] += 1
                if pl: pl["goals"] += 1
        elif typ == "tackle":
            ts["tackles"] += 1
            if pl: pl["tackles"] += 1
            items["def_" + team] = 1.0
            if succ:
                ts["tackles_won"] += 1
                if pl: pl["tackles_won"] += 1
        elif typ == "interception":
            ts["interceptions"] += 1
            items["def_" + team] = 1.0
            if pl: pl["interceptions"] += 1
        elif typ == "pressure":
            ts["pressures"] += 1
            if pl: pl["pressures"] += 1
        elif typ == "possession_change":
            self.t[_other(team)]["turnovers"] += 1       # the team that LOST it
            items["turn"] = 1.0
        elif typ == "corner":
            ts["corners"] += 1
            self._mom(team, 0.3)
        elif typ == "foul":
            ts["fouls"] += 1
            items["def_" + team] = 1.0
        elif typ == "red_card":
            self.reds[team] += 1
            if pl: pl["on"] = False
        elif typ == "sprint":
            spd = float(e.get("player_speed") or 0)
            if pl:
                pl["sprints"] += 1
                pl["top_speed"] = max(pl["top_speed"], spd)
                pl["dist_m"] += e.get("distance_m") or 0
        if pl and e.get("player_speed"):
            pl["top_speed"] = max(pl["top_speed"], float(e["player_speed"]))
        self._win_add(el, items)

        minute_f = el / 60.0
        wp = win_probability(self.score, self.xg, minute_f, self.reds)
        swing = {k: wp[k] - self.wp_prev[k] for k in wp}
        self.wp_prev = wp

        # ---------------- signals (story / insight / milestone) ----------------
        base = dict(team=team, player=name, event_id=e.get("id"), clock=e.get("clock", ""))
        if typ == "goal":
            signals.append(dict(base, kind="story", key="goal", priority=5, params=self._pp(e)))
            if pl and pl["goals"] == 2: signals.append(dict(base, kind="milestone", key="brace", priority=4, params={"player": name}))
            if pl and pl["goals"] >= 3: signals.append(dict(base, kind="milestone", key="hattrick", priority=5, params={"player": name}))
        elif typ == "red_card":
            signals.append(dict(base, kind="story", key="red", priority=5, params=self._pp(e)))
        elif typ == "yellow_card":
            signals.append(dict(base, kind="story", key="yellow", priority=2, params=self._pp(e)))
        elif typ == "shot_saved" and (e.get("xg") or 0) >= 0.2:
            signals.append(dict(base, kind="story", key="chance", priority=3, params=self._pp(e)))
        elif typ == "half_time":
            signals.append(dict(base, kind="story", key="halftime", priority=3, params=self._pp(e)))
        elif typ == "full_time":
            signals.append(dict(base, kind="story", key="fulltime", priority=4, params=self._pp(e)))
        if typ in ("goal", "shot_saved", "shot_blocked", "shot_off_target") and (e.get("shot_speed") or 0) > max(108.0, self.match_top_shot):
            self.match_top_shot = e["shot_speed"]
            signals.append(dict(base, kind="milestone", key="shotspeed", priority=3,
                                params={"player": name, "speed": round(e["shot_speed"])}))
        if typ == "sprint":
            spd = float(e.get("player_speed") or 0)
            if spd >= 31:
                signals.append(dict(base, kind="story", key="sprint", priority=2, params={"player": name, "speed": round(spd, 1)}))
            if spd > self.match_top_speed and spd >= 33:
                signals.append(dict(base, kind="milestone", key="topspeed", priority=3, params={"player": name, "speed": round(spd, 1)}))
            self.match_top_speed = max(self.match_top_speed, spd)
            tag_extra["speed_kmh"] = round(spd, 1)
        if pl and typ == "pass" and succ and pl["passes_ok"] in (25, 50, 75):
            acc = round(pl["passes_ok"] / pl["passes"] * 100)
            signals.append(dict(base, kind="milestone", key="passes", priority=2, params={"player": name, "n": pl["passes_ok"], "acc": acc}))
        if pl and typ == "tackle" and succ and pl["tackles_won"] in (3, 5):
            signals.append(dict(base, kind="milestone", key="tackles", priority=2, params={"player": name, "n": pl["tackles_won"]}))
        for tk in ("home", "away"):
            while self.xg[tk] >= self.xg_marks[tk] + 1.0:
                self.xg_marks[tk] += 1
                signals.append(dict(base, team=tk, player="", kind="milestone", key="xgmark", priority=2,
                                    params={"team": self.teams[tk]["name"], "xg": self.xg_marks[tk]}))
        for p in self.players.values():                      # distance thresholds -> overlay indicator
            for mk in DIST_MARKS:
                if p["dist_m"] >= mk * 1000 and mk not in p["marks"] and p["on"]:
                    p["marks"].add(mk)
                    signals.append(dict(base, team=p["team"], player=p["name"], kind="milestone", key="distance", priority=2,
                                        params={"player": p["name"], "km": mk}))
                    if p["name"] == name: tag_extra["distance_km"] = mk
        signals.extend(self._insights(el, e))

        # ---------------- overlays (language-neutral, machine-readable) ----------------
        eid = e.get("id", self.seq)
        absx = (lambda x: None if x is None else (x if team == "home" else 100 - x))
        if typ in OVERLAY_EVENTS and name:
            tag = {"player": name, "team": team, "role": pl["role"] if pl else "", "ax": absx(e.get("x")), "ay": absx(e.get("y")),
                   "event": typ}
            tag.update(tag_extra)
            if typ == "sprint" or "distance_km" in tag_extra:
                tag["indicator"] = True
            overlays.append(self._ov(eid, "player_tag", el, 2500, 2, tag))
        if typ == "pass" and ((e.get("difficulty") or 0) >= 0.55 or (e.get("distance_m") or 0) >= 38 or (e.get("ball_speed") or 0) >= 88):
            overlays.append(self._ov(eid, "pass_card", el, 3500, 3, {
                "player": name, "to_player": e.get("to_player", ""), "team": team, "success": bool(succ),
                "distance_m": e.get("distance_m"), "difficulty_10": round((e.get("difficulty") or 0) * 10, 1),
                "ball_speed_kmh": e.get("ball_speed"), "player_pass_acc": round(pl["passes_ok"] / pl["passes"] * 100) if pl and pl["passes"] else None,
                "ax": absx(e.get("x")), "ay": absx(e.get("y")),
                "ato_x": absx(e.get("to_x")), "ato_y": absx(e.get("to_y"))}))
        if typ in ("shot_saved", "shot_blocked", "shot_off_target", "goal"):
            overlays.append(self._ov(eid, "shot_card", el, 4500, 4, {
                "player": name, "team": team, "xg": e.get("xg"), "shot_speed_kmh": e.get("shot_speed"), "outcome": typ,
                "distance_m": e.get("distance_m"), "ax": absx(e.get("x")), "ay": absx(e.get("y"))}))
        for s in signals:
            if s["priority"] >= 3 or s["kind"] == "insight":
                overlays.append(self._ov("%s-%s" % (eid, s["key"]), "lower_third", el, 5000 if s["priority"] >= 4 else 4000,
                                         s["priority"], {"signal": s["key"], "kind": s["kind"], "team": s["team"], "player": s["player"]}))

        # ---------------- snapshot stored on the event (for batch analytics) ----------------
        ph, pa = self.t["home"]["poss_s"], self.t["away"]["poss_s"]
        e["state"] = {
            "t": round(minute_f, 2), "xg": {k: round(v, 2) for k, v in self.xg.items()}, "score": dict(self.score),
            "shots": {k: self.t[k]["shots"] for k in self.t}, "momentum": round(self.mom, 3),
            "wp": {k: round(v, 4) for k, v in wp.items()}, "wp_swing": {k: round(v, 4) for k, v in swing.items()},
            "poss": round(ph / ((ph + pa) or 1) * 100, 1), "phase": self.phase,
        }
        for s in signals:
            s["id"] = "%s-%s" % (eid, s["key"])
            s["elapsed"] = el
        frame = {"seq": self.seq, "event": self.slim(e), "state": self.snapshot(e), "signals": signals, "overlays": overlays,
                 "latency_ms": round((time.perf_counter() - t0) * 1000, 3)}
        self.last_frame = frame
        return frame

    # ------------------------------------------------------------------ helpers
    def _mom(self, team, v): self.mom += v if team == "home" else -v

    def _pp(self, e):
        h, a = self.teams["home"]["name"], self.teams["away"]["name"]
        return {"player": e.get("player", ""), "team": self.teams[e["team"]]["name"], "clock": e.get("clock", ""),
                "home": h, "away": a, "hs": self.score["home"], "as": self.score["away"], "xg": e.get("xg", 0)}

    def _ov(self, oid, typ, el, dur, prio, payload):
        return {"id": "%s-%s" % (oid, typ), "type": typ, "t_start_s": round(el, 1), "duration_ms": dur, "priority": prio,
                "payload": payload}

    def slim(self, e):
        keys = ("id", "elapsed", "clock", "minute", "team", "poss", "player", "type", "x", "y", "to_x", "to_y", "to_player",
                "success", "distance_m", "difficulty", "ball_speed", "shot_speed", "player_speed", "pressure", "xg", "detail")
        return {k: e.get(k) for k in keys}

    def snapshot(self, e=None):
        ph, pa = self.t["home"]["poss_s"], self.t["away"]["poss_s"]
        return {"clock": (e or {}).get("clock", ""), "elapsed": round(self.last_el, 1), "score": dict(self.score),
                "xg": {k: round(v, 2) for k, v in self.xg.items()},
                "shots": {k: self.t[k]["shots"] for k in self.t}, "sot": {k: self.t[k]["sot"] for k in self.t},
                "poss": round(ph / ((ph + pa) or 1) * 100, 1), "wp": {k: round(v, 3) for k, v in self.wp_prev.items()},
                "momentum": round(self.mom, 2), "phase": self.phase,
                "control": {k: self.control(k) for k in ("home", "away")},
                "pass_acc": {k: round(self.t[k]["passes_ok"] / self.t[k]["passes"] * 100, 1) if self.t[k]["passes"] else 0
                             for k in self.t},
                "top_speed": round(self.match_top_speed, 1)}

    # ------------------------------------------------------------------ explainability
    def _insights(self, el, e) -> List[Dict]:
        """Control vs chaos, pressing intensity and momentum flips - each with its numeric evidence."""
        out: List[Dict] = []
        if el - self.last_eval < 20 or el < 480:
            return out
        self.last_eval = el
        win_min = min(WINDOW_S, el) / 60.0
        turns = self._w("turn")
        rate = turns / win_min
        ctl = {k: self.control(k) for k in ("home", "away")}
        new = "balanced"
        for k in ("home", "away"):
            if ctl[k]["poss"] >= 60 and ctl[k]["pass_acc"] >= 85:
                new = k + "_control"
        if new == "balanced" and rate >= CHAOS_RATE and max(ctl["home"]["poss"], ctl["away"]["poss"]) < 54:
            new = "chaos"
        base = dict(player="", event_id=e.get("id"), clock=e.get("clock", ""), kind="insight")
        if new != self.phase and el - self.phase_since > 300:
            self.phase, self.phase_since = new, el
            if new in ("home_control", "away_control"):
                k = new.split("_")[0]
                out.append(dict(base, team=k, key="control", priority=3, params={
                    "team": self.teams[k]["name"], "poss": round(ctl[k]["poss"]), "acc": round(ctl[k]["pass_acc"]),
                    "mins": round(win_min)}, evidence=ctl[k]))
            elif new == "chaos":
                out.append(dict(base, team="", key="chaos", priority=3, params={"n": int(turns), "mins": round(win_min)},
                                evidence={"turnovers_per_min": round(rate, 2)}))
        for k in ("home", "away"):
            c = ctl[k]
            hi = c["def_actions"] >= 12 and c["ppda"] <= PRESS_PPDA
            if hi and not self.press[k] and el - self.press_at[k] > 900:
                self.press_at[k] = el
                out.append(dict(base, team=k, key="press", priority=3, params={"team": self.teams[k]["name"], "ppda": c["ppda"]},
                                evidence=c))
            self.press[k] = hi
        sign = 1 if self.mom > 1.6 else (-1 if self.mom < -1.6 else 0)
        if sign and sign != self.mom_sign and el - self.mom_flip_at > 300 and self.mom_sign != 0:
            k = "home" if sign > 0 else "away"
            out.append(dict(base, team=k, key="momentum", priority=3,
                            params={"team": self.teams[k]["name"], "mom": round(abs(self.mom), 1)}, evidence={"momentum": round(self.mom, 2)}))
            self.mom_flip_at = el
        if sign:
            self.mom_sign = sign
        return out

    # ------------------------------------------------------------------ views
    def player_card(self, name):
        p = self.players.get(name)
        if not p:
            return None
        return {"name": name, "team": p["team"], "role": p["role"], "passes": p["passes"],
                "pass_acc": round(p["passes_ok"] / p["passes"] * 100) if p["passes"] else None,
                "avg_pass_m": round(p["pass_dist"] / p["passes"], 1) if p["passes"] else None,
                "avg_difficulty": round(p["diff_sum"] / p["passes"] * 10, 1) if p["passes"] else None,
                "shots": p["shots"], "sot": p["sot"], "goals": p["goals"], "xg": round(p["xg"], 2),
                "tackles_won": p["tackles_won"], "interceptions": p["interceptions"], "pressures": p["pressures"],
                "distance_km": round(p["dist_m"] / 1000, 2), "top_speed": round(p["top_speed"], 1), "sprints": p["sprints"],
                "top_shot_kmh": round(p["top_shot"], 1)}

    def leaders(self, metric, n=3):
        key = {"speed": ("top_speed", "km/h"), "distance": ("dist_m", "km"), "xg": ("xg", "xG"), "shots": ("shots", ""),
               "tackles": ("tackles_won", ""), "passes": ("passes_ok", "")}.get(metric)
        if not key:
            return []
        rows = sorted(self.players.values(), key=lambda p: -p[key[0]])[:n]
        return [{"player": p["name"], "team": p["team"], "value": round(p[key[0]] / 1000, 2) if key[0] == "dist_m" else round(p[key[0]], 2),
                 "unit": key[1]} for p in rows if p[key[0]] > 0]


# thresholds calibrated on the synthetic generator (tune/recalibrate for the official dataset)
CHAOS_RATE = 2.8       # turnovers/min (about the 90th percentile on synthetic data)
PRESS_PPDA = 3.0       # opponent passes per defensive action (about the 10th percentile)
