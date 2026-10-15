"""Microsoft Fabric / OneLake export.

Writes events, key moments and the match fact sheet as files into a Fabric Lakehouse (Files/pitchside/...),
where a notebook or shortcut can load them into Delta tables for Power BI / Direct Lake. Falls back to ./export."""
import csv
import io
import json
import os
from typing import Dict

from . import key_moments, match_facts

EVENT_COLS = ["id", "elapsed", "clock", "minute", "team", "poss", "player", "to_player", "type", "x", "y", "to_x", "to_y", "success",
              "distance_m", "difficulty", "ball_speed", "shot_speed", "player_speed", "pressure", "xg", "detail"]


def _players_csv(match: Dict) -> str:
    buf = io.StringIO()
    cards = [match["engine"].player_card(n) for n in match["engine"].players] if match.get("engine") else []
    if cards:
        w = csv.DictWriter(buf, fieldnames=list(cards[0]))
        w.writeheader()
        w.writerows(cards)
    return buf.getvalue()


def _payloads(match: Dict, seed: int) -> Dict[str, str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EVENT_COLS + ["home_goals", "away_goals", "home_xg", "away_xg", "momentum", "wp_home", "wp_draw", "wp_away"])
    for e in match["events"]:
        s = e["state"]
        w.writerow([e[c] for c in EVENT_COLS] + [s["score"]["home"], s["score"]["away"], s["xg"]["home"],
                                                 s["xg"]["away"], s["momentum"], s["wp"]["home"], s["wp"]["draw"], s["wp"]["away"]])
    return {
        "events.csv": buf.getvalue(),
        "players.csv": _players_csv(match),
        "key_moments.json": json.dumps(key_moments(match, top=12), indent=1),
        "match_facts.json": json.dumps(match_facts(match), indent=1),
    }


def export(match: Dict, seed: int) -> Dict:
    base = "pitchside/match_%d" % seed
    files = _payloads(match, seed)
    ws, lh = os.getenv("FABRIC_WORKSPACE"), os.getenv("FABRIC_LAKEHOUSE")
    if ws and lh:
        from azure.identity import DefaultAzureCredential
        from azure.storage.filedatalake import DataLakeServiceClient
        svc = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=DefaultAzureCredential())
        fs = svc.get_file_system_client(ws)
        for name, data in files.items():
            fs.get_file_client("%s.Lakehouse/Files/%s/%s" % (lh, base, name)).upload_data(data.encode(), overwrite=True)
        return {"target": "onelake", "path": "%s/%s.Lakehouse/Files/%s" % (ws, lh, base), "files": list(files)}
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "export", "match_%d" % seed)
    os.makedirs(out, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(out, name), "w") as f:
            f.write(data)
    return {"target": "local", "path": out, "files": list(files)}
