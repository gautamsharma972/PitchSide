"""Synthetic dataset factory: many football-realistic matches (varied styles/strengths) + realism report."""
import csv
import os
import random
from typing import Dict

from . import enrich, generate_match

COLS = ["match", "id", "elapsed", "clock", "minute", "period", "team", "poss", "player", "to_player", "type", "x", "y",
        "to_x", "to_y", "success", "distance_m", "difficulty", "ball_speed", "shot_speed", "player_speed", "pressure",
        "xg", "detail"]


def generate_dataset(n: int, base_seed: int, out_dir: str) -> Dict:
    os.makedirs(out_dir, exist_ok=True)
    rows, agg = 0, {"goals": 0, "shots": 0, "xg": 0.0, "passes": 0, "pass_ok": 0, "cards": 0, "events": 0}
    summaries = []
    with open(os.path.join(out_dir, "events.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for i in range(n):
            seed = base_seed + i
            m = enrich(generate_match(seed))
            tot = {"goals": 0, "shots": 0, "xg": 0.0, "passes": 0, "pass_ok": 0, "cards": 0}
            for e in m["events"]:
                w.writerow([seed] + [e.get(c) for c in COLS[1:]])
                rows += 1
                tot["goals"] += e["type"] == "goal"
                tot["shots"] += e["type"].startswith("shot") or e["type"] == "goal"
                tot["xg"] += e["xg"]
                tot["passes"] += e["type"] == "pass"
                tot["pass_ok"] += bool(e["type"] == "pass" and e["success"])
                tot["cards"] += e["type"] in ("yellow_card", "red_card")
            for k in tot: agg[k] += tot[k]
            agg["events"] += len(m["events"])
            summaries.append({"match": seed, "score": m["events"][-1]["state"]["score"], "styles": m["meta"]["styles"],
                              "events": len(m["events"])})
    with open(os.path.join(out_dir, "matches.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["match", "home_goals", "away_goals", "home_style", "away_style", "events"])
        for s in summaries:
            w.writerow([s["match"], s["score"]["home"], s["score"]["away"], s["styles"]["home"], s["styles"]["away"], s["events"]])
    per = {k: round(v / n, 2) for k, v in agg.items()}
    per["pass_accuracy_pct"] = round(agg["pass_ok"] / max(1, agg["passes"]) * 100, 1)
    return {"matches": n, "events": rows, "path": out_dir, "per_match_averages": per,
            "real_world_reference": {"goals": "~2.7", "shots": "~25", "passes": "~900-1000", "pass_accuracy_pct": "~80-85"}}
