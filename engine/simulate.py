"""Synthetic, football-realistic match generator (seeded, reproducible).

Possession-driven simulation: every ~3-6 s of play the team in possession passes, takes on a defender, sprints or
shoots; defenders apply pressure, tackle and intercept. Events carry the fields a tracking/event feed would:
pass distance/difficulty/ball speed, shot speed + xG, player speed, pressure, possession owner, match metadata.

Swap `load_match()` in engine/__init__.py for the official dataset loader (same schema) - the live pipeline is unchanged.
"""
import math
import random
from typing import Dict, List

PITCH_L, PITCH_W = 105.0, 68.0
ROLES = ["GK", "RB", "CB", "CB", "LB", "DM", "CM", "AM", "RW", "ST", "LW"]
SHOOT_W = [0.0, 0.5, 0.6, 0.6, 0.5, 1.0, 1.4, 2.6, 3.0, 4.0, 2.6]
PASS_W = [0.5, 1.2, 1.0, 1.0, 1.2, 1.6, 1.8, 1.5, 1.3, 1.0, 1.3]
SPRINT_W = [0.0, 1.5, 0.6, 0.6, 1.5, 0.8, 1.0, 1.0, 3.0, 2.5, 3.0]

TEAMS = {
    "home": {
        "name": "Northbridge FC", "short": "NOR", "strength": 1.12, "formation": "4-3-3",
        "players": ["Dan Hargreave", "Tomas Reyes", "Jamal Okoye", "Luca Ferrante", "Ben Alcott",
                    "Idris Bello", "Marco Villani", "Sam Pritchard", "Owen Tadesse", "Kane Whitlock", "Jack Merrow"],
    },
    "away": {
        "name": "Harlow United", "short": "HAR", "strength": 1.0, "formation": "4-2-3-1",
        "players": ["Joel Ashby", "Eli Rosenthal", "Kofi Mensah", "Pedro Navarro", "Callum Dray",
                    "Yusuf Demir", "Anders Lund", "Rhys Penhale", "Mateo Ibarra", "Viktor Hale", "Theo Brandt"],
    },
}
# fwd = mean forward metres-ish progress per pass (pitch %), len = pass length spread, shot = shot propensity,
# press = pressing intensity, tempo = steps per unit time
STYLES = {
    "possession": {"fwd": 3.0, "len": 11.0, "shot": 0.95, "press": 0.45, "tempo": 1.05},
    "direct": {"fwd": 8.5, "len": 18.0, "shot": 1.10, "press": 0.35, "tempo": 0.9},
    "pressing": {"fwd": 5.5, "len": 13.0, "shot": 1.05, "press": 0.85, "tempo": 1.0},
}


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


class _Sim:
    def __init__(self, seed: int, styles=None):
        self.seed = seed
        self.rng = random.Random(seed)
        r = self.rng
        names = sorted(STYLES)
        styles = styles or {"home": r.choice(names), "away": r.choice(names)}
        self.teams = {k: dict(v, style=styles[k]) for k, v in TEAMS.items()}
        self.s1, self.s2 = r.randint(1, 4), r.randint(3, 6)
        self.h1, self.h2 = 2700 + self.s1 * 60, 2700 + self.s2 * 60
        self.events: List[Dict] = []
        self.floor, self.limit = 0.0, 1e9
        self.score = {"home": 0, "away": 0}
        self.reds = {"home": 0, "away": 0}
        self.off = {"home": set(), "away": set()}
        self.yellows = {}
        self.subs_done = set()
        self.poss = r.choice(["home", "away"])
        self.bx, self.by, self.holder = 50.0, 50.0, 6
        self.meta = {
            "venue": "Northbridge Park", "referee": "A. Kowalczyk", "attendance": r.randint(28000, 59000),
            "weather": r.choice(["clear", "light rain", "overcast", "windy"]), "kickoff_utc": "2026-10-17T15:00:00Z",
            "competition": "Synthetic League", "styles": styles,
            "stoppage": {"first_half": self.s1, "second_half": self.s2},
        }

    # ---- helpers ----
    def other(self, t): return "away" if t == "home" else "home"

    def strength(self, t):
        o = self.other(t)
        s = self.teams[t]["strength"] * (1.0 + 0.10 * (self.score[o] - self.score[t]))
        s *= 0.62 if self.reds[t] else 1.0
        s *= 1.25 if self.reds[o] else 1.0
        return max(0.35, s)

    def sty(self, t): return STYLES[self.teams[t]["style"]]

    def name(self, t, i): return self.teams[t]["players"][i]

    def pick(self, t, weights, exclude=()):
        idx = [i for i in range(11) if i not in exclude and i not in self.off[t]]
        w = [weights[i] for i in idx]
        return self.rng.choices(idx, weights=w)[0] if sum(w) > 0 else idx[0]

    def clock(self, el, period):
        if period == 1:
            if el >= 2700:
                return 45, "45+%d'" % (int((el - 2700) // 60) + 1)
            m = int(el // 60) + 1
            return m, "%d'" % m
        g = 2700 + (el - self.h1)
        if g >= 5400:
            return 90, "90+%d'" % (int((g - 5400) // 60) + 1)
        m = int(g // 60) + 1
        return m, "%d'" % m

    def add(self, el, period, team, etype, player="", **kw):
        el = max(min(el, self.limit), self.floor)
        self.floor = el
        minute, label = self.clock(el, period)
        e = {"id": len(self.events) + 1, "elapsed": round(el, 1), "period": period, "minute": minute, "clock": label,
             "second": int(el % 60), "team": team, "poss": self.poss, "player": player, "type": etype,
             "x": None, "y": None, "to_x": None, "to_y": None, "to_player": "", "success": None,
             "distance_m": None, "difficulty": None, "ball_speed": None, "shot_speed": None,
             "player_speed": None, "pressure": 0, "xg": 0.0, "detail": "", "score": dict(self.score)}
        e.update(kw)
        self.events.append(e)
        return e

    # ---- main loop ----
    def run(self) -> Dict:
        self.add(0, 1, self.poss, "kickoff")
        self._period(0, self.h1, 1)
        self.limit = self.h1
        self.add(self.h1, 1, "home", "half_time")
        self.poss = self.other(self.poss)
        self.bx, self.by, self.holder = 50.0, 50.0, 6
        self.limit = 1e9
        self.add(self.h1, 2, self.poss, "kickoff")
        self._period(self.h1, self.h1 + self.h2, 2)
        self.limit = 1e9
        self.add(self.h1 + self.h2, 2, "home", "full_time")
        return {"teams": self.teams, "events": self.events, "seed": self.seed, "meta": self.meta,
                "total_elapsed": self.h1 + self.h2}

    def _period(self, start, end, period):
        el = start
        self.limit = end - 0.1
        self.floor = start
        while True:
            el = max(el, self.floor) + self.rng.uniform(3.0, 6.5) / self.sty(self.poss)["tempo"]
            if el >= end:
                break
            self._maybe_sub(el, period)
            self._step(el, period)

    def _maybe_sub(self, el, period):
        if period != 2:
            return
        minute, _ = self.clock(el, period)
        for m in (62, 70, 78):
            if minute >= m and m not in self.subs_done:
                self.subs_done.add(m)
                t = self.rng.choice(["home", "away"])
                self.add(el, period, t, "substitution", self.name(t, self.rng.randint(5, 10)), detail="tactical change")

    def _step(self, el, period):
        r, t = self.rng, self.poss
        o = self.other(t)
        if r.random() < 0.022:
            self._foul(el, period)
        if r.random() < 0.04:
            self._sprint(el, period)
        pn = 0
        if r.random() < 0.12 + 0.32 * self.sty(o)["press"] * self.strength(o) / 1.1:
            pn = 1 + (r.random() < 0.35) + (r.random() < 0.12)
            d = self.pick(o, [0, 1, 1, 1, 1, 2, 2, 2, 2, 1.5, 1.5])
            self.add(el, period, o, "pressure", self.name(o, d), x=round(100 - self.bx, 1), y=round(100 - self.by, 1),
                     pressure=pn, player_speed=round(r.uniform(14, 26), 1), to_player=self.name(t, self.holder))
            if r.random() < 0.22:
                won = r.random() < 0.40 * self.strength(o) / self.strength(t)
                self.add(el + 0.5, period, o, "tackle", self.name(o, d), success=won, to_player=self.name(t, self.holder),
                         x=round(100 - self.bx, 1), y=round(100 - self.by, 1),
                         detail="tackle won" if won else "tackle evaded")
                if won:
                    self._turnover(el + 0.5, period, "tackle", self.bx, self.by)
                    return
        if self.bx > 66 and r.random() < 0.07 * self.strength(t) * self.sty(t)["shot"] * (self.bx - 58) / 42:
            self._shot(el, period)
            return
        self._pass(el, period, pn)

    def _pass(self, el, period, pn):
        r, t = self.rng, self.poss
        o = self.other(t)
        sty, s = self.sty(t), self.strength(t)
        h = self.holder
        rcv = self.pick(t, PASS_W, exclude=(h,))
        dx = r.gauss(sty["fwd"] * (0.7 + 0.3 * s), sty["len"] * 0.8)
        if r.random() < 0.07 * sty["len"] / 11:
            dx += 22
        tx = _clip(self.bx + dx, 2, 93)
        ty = _clip(self.by + r.gauss(0, 16), 6, 94)
        dist = math.hypot((tx - self.bx) * PITCH_L / 100, (ty - self.by) * PITCH_W / 100)
        if dist < 4.0:                                   # never a zero-length pass (ball pinned at a boundary)
            tx = self.bx - 8.0 if self.bx > 50 else self.bx + 8.0
            dist = math.hypot((tx - self.bx) * PITCH_L / 100, (ty - self.by) * PITCH_W / 100)
        diff = _clip(0.05 + dist / 75 * 0.45 + 0.10 * pn + max(0.0, tx - self.bx) / 100 * 0.8
                     + (0.08 if tx > 75 else 0) + r.gauss(0, 0.05), 0.02, 0.98)
        p_ok = _clip(0.99 - 0.55 * diff, 0.25, 0.97) * (0.93 + 0.06 * s)
        ok = r.random() < p_ok
        speed = dist / (0.3 + dist / r.uniform(15, 24)) * 3.6
        self.add(el, period, t, "pass", self.name(t, h), x=round(self.bx, 1), y=round(self.by, 1), to_x=round(tx, 1),
                 to_y=round(ty, 1), to_player=self.name(t, rcv) if ok else "", success=ok, distance_m=round(dist, 1),
                 difficulty=round(diff, 2), ball_speed=round(speed, 1), pressure=pn)
        if ok:
            self.bx, self.by, self.holder = tx, ty, rcv
        else:
            d = self.pick(o, [0, 1, 1, 1, 1, 2, 2, 2, 2, 1.5, 1.5])
            self.add(el + 1.0, period, o, "interception", self.name(o, d), x=round(100 - tx, 1), y=round(100 - ty, 1),
                     detail="pass intercepted")
            self._turnover(el + 1.0, period, "interception", tx, ty, new_holder=d)

    def _turnover(self, el, period, reason, bx, by, new_holder=None):
        o = self.other(self.poss)
        self.poss = o
        self.bx, self.by = 100 - bx, 100 - by
        self.holder = new_holder if new_holder is not None else self.pick(o, [0, 1, 1, 1, 1, 2, 2, 2, 2, 1.5, 1.5])
        self.add(el, period, o, "possession_change", self.name(o, self.holder), x=round(self.bx, 1), y=round(self.by, 1),
                 detail=reason)

    def _sprint(self, el, period):
        r, t = self.rng, self.poss
        p = self.pick(t, SPRINT_W)
        self.add(el, period, t, "sprint", self.name(t, p), x=round(self.bx, 1), y=round(self.by, 1),
                 player_speed=round(_clip(r.gauss(29.5, 2.4), 24.0, 35.6), 1), distance_m=round(r.uniform(14, 48), 1))

    def _foul(self, el, period):
        r, t = self.rng, self.poss
        o = self.other(t)
        p = self.pick(o, [0, 1, 1, 1, 1, 2, 2, 2, 2, 1.5, 1.5], exclude=(0,))
        pname = self.name(o, p)
        self.add(el, period, o, "foul", pname, detail="foul committed", x=round(100 - self.bx, 1), y=round(100 - self.by, 1))
        if r.random() < 0.15:
            if r.random() < 0.012 and not self.reds[o] and el > 900:
                self.reds[o] += 1
                self.off[o].add(p)
                self.add(el, period, o, "red_card", pname, detail="sent off for a reckless challenge")
            elif self.yellows.get(pname):
                self.reds[o] += 1
                self.off[o].add(p)
                self.add(el, period, o, "yellow_card", pname, detail="second booking")
                self.add(el, period, o, "red_card", pname, detail="sent off (second yellow)")
            else:
                self.yellows[pname] = True
                self.add(el, period, o, "yellow_card", pname, detail="booked")

    def _shot(self, el, period, header=False, sx=None, sy=None):
        r, t = self.rng, self.poss
        o = self.other(t)
        x = self.bx if sx is None else sx
        y = self.by if sy is None else sy
        shooter = self.pick(t, SHOOT_W)
        dxm, dym = (100 - x) * PITCH_L / 100, (y - 50) * PITCH_W / 100
        dist = math.hypot(dxm, dym)
        base = 0.40 / (1 + math.exp(0.20 * (dist - 15))) + 0.01
        angle = 0.5 + 0.5 * math.cos(min(1.25, math.atan2(abs(dym), dxm + 0.1)))
        xg = round(_clip(base * angle * (0.8 if header else 1.0), 0.02, 0.8), 2)
        spd = round(r.uniform(62, 98) if header else r.uniform(72, 128) * (1.0 - min(dist, 30) / 200), 1)
        roll = r.random()
        common = dict(x=round(x, 1), y=round(y, 1), xg=xg, shot_speed=spd, distance_m=round(dist, 1),
                      to_x=100.0, to_y=round(_clip(50 + r.gauss(0, 7), 40, 60), 1))
        p, nm = shooter, self.name(t, shooter)
        if roll < xg:
            self.score[t] += 1
            kind = r.choice(["Header", "Volley", "Low drive", "Curler", "Finish"]) if not header else "Header"
            self.add(el, period, t, "goal", nm, detail="%s from %dm" % (kind, dist), **common)
            self.poss, self.bx, self.by, self.holder = o, 50.0, 50.0, 6
            self.add(el + 40, period, o, "possession_change", self.name(o, 6), detail="kickoff", x=50.0, y=50.0)
            return
        if roll < xg + 0.28:
            gk = self.name(o, 0)
            self.add(el, period, t, "shot_saved", nm, detail="saved by %s" % gk, **common)
            if r.random() < 0.28:
                self._corner(el + 6, period)
                return
            reason = "save"
        elif roll < xg + 0.50:
            self.add(el, period, t, "shot_blocked", nm, detail="blocked", **common)
            if r.random() < 0.38:
                self._corner(el + 6, period)
                return
            reason = "block"
        else:
            self.add(el, period, t, "shot_off_target", nm, detail="off target", **common)
            if r.random() < 0.10:
                self._corner(el + 6, period)
                return
            reason = "goal kick"
        self.poss = o
        self.bx, self.by, self.holder = 12.0, 50.0, 0
        self.add(el + 3, period, o, "possession_change", self.name(o, 0), detail=reason, x=12.0, y=50.0)

    def _corner(self, el, period):
        t = self.poss
        self.add(el, period, t, "corner", self.name(t, self.pick(t, PASS_W)), x=100.0, y=self.rng.choice([2.0, 98.0]))
        if self.rng.random() < 0.38:
            self._shot(el + 4, period, header=True, sx=94.0, sy=50 + self.rng.gauss(0, 7))
        else:
            self.bx, self.by, self.holder = 70.0, 50.0, self.pick(t, PASS_W)


def generate_match(seed: int = 3, styles=None) -> Dict:
    return _Sim(seed, styles).run()
