"""Auto-eventing: derive pass / speed metrics from a tracking stream instead of hand-tagging.

Production path: a vision model (e.g. a detector/tracker served from Azure AI Foundry / Azure AI Vision on the
broadcast feed) emits per-frame ball position + the id of the player in possession. `detect_passes()` turns that
stream into pass events with distance, ball speed and success - the same schema the pipeline already consumes.

Here the tracking stream is *synthesised* from the match's ground-truth events (10 Hz ball track), so the
detector can be evaluated end to end without video. Swap `synth_tracking()` for real model output."""
import math
import random
from typing import Dict, List

HZ = 10
L, W = 105.0, 68.0


def _abs(team, x, y):
    ax, ay = (x, y) if team == "home" else (100 - x, 100 - y)
    return ax * L / 100, ay * W / 100


def synth_tracking(match: Dict, start_s: float, end_s: float, noise_m: float = 0.35, seed: int = 1) -> List[Dict]:
    """Ball frames {t, x, y, owner, team}; owner=None while the ball is in flight. Gaussian position noise
    (default 0.35 m, typical of broadcast-camera calibration error) is added to every frame."""
    rng = random.Random(seed)
    frames: List[Dict] = []
    for e in match["events"]:
        if e["type"] != "pass" or not (start_s <= e["elapsed"] < end_s):
            continue
        x0, y0 = _abs(e["team"], e["x"], e["y"])
        x1, y1 = _abs(e["team"], e["to_x"], e["to_y"])
        flight = max(0.2, (e["distance_m"] or 1) / ((e["ball_speed"] or 50) / 3.6))
        t = e["elapsed"]
        frames.append({"t": round(t - 0.1, 2), "x": x0, "y": y0, "owner": e["player"], "team": e["team"]})
        n = max(2, int(flight * HZ))
        for i in range(1, n):
            f = i / n
            frames.append({"t": round(t + f * flight, 2), "x": x0 + (x1 - x0) * f, "y": y0 + (y1 - y0) * f, "owner": None, "team": None})
        if e["success"]:
            frames.append({"t": round(t + flight, 2), "x": x1, "y": y1, "owner": e["to_player"], "team": e["team"]})
        else:
            nxt = next((q for q in match["events"] if q["id"] > e["id"] and q["type"] == "interception"), None)
            frames.append({"t": round(t + flight, 2), "x": x1, "y": y1, "owner": nxt["player"] if nxt else "?",
                           "team": "away" if e["team"] == "home" else "home"})
    for f in frames:
        f["x"] += rng.gauss(0, noise_m)
        f["y"] += rng.gauss(0, noise_m)
    return frames


def detect_passes(frames: List[Dict]) -> List[Dict]:
    """Possession hand-offs in the ball track -> passes with distance, ball speed and completion."""
    out, last = [], None                                   # last = (owner, team, t, x, y)
    flight_start = None
    for f in frames:
        if f["owner"]:
            if last and f["owner"] != last[0] and flight_start:
                t0, x0, y0 = flight_start
                d = math.hypot(f["x"] - x0, f["y"] - y0)
                dur = max(f["t"] - t0, 1e-3)
                out.append({"t": t0, "player": last[0], "to_player": f["owner"], "team": last[1], "success": f["team"] == last[1],
                            "distance_m": round(d, 1), "ball_speed": round(d / dur * 3.6, 1)})
            last, flight_start = (f["owner"], f["team"], f["t"], f["x"], f["y"]), None
        elif last and flight_start is None:
            flight_start = (last[2], last[3], last[4])
    return out


def evaluate(match: Dict, start_s: float = 600, end_s: float = 900) -> Dict:
    truth = [e for e in match["events"] if e["type"] == "pass" and start_s <= e["elapsed"] < end_s]
    det = detect_passes(synth_tracking(match, start_s, end_s))
    hit, d_err, v_err = 0, [], []
    used = set()
    for g in truth:
        c = next((i for i, d in enumerate(det) if i not in used and d["player"] == g["player"] and abs(d["t"] - g["elapsed"]) < 1.5), None)
        if c is None:
            continue
        used.add(c)
        d = det[c]
        hit += 1
        d_err.append(abs(d["distance_m"] - g["distance_m"]))
        v_err.append(abs(d["ball_speed"] - g["ball_speed"]))
    return {"window_s": [start_s, end_s], "truth_passes": len(truth), "detected": len(det), "matched": hit,
            "recall": round(hit / len(truth), 3) if truth else None,
            "precision": round(hit / len(det), 3) if det else None,
            "distance_mae_m": round(sum(d_err) / len(d_err), 2) if d_err else None,
            "ball_speed_mae_kmh": round(sum(v_err) / len(v_err), 2) if v_err else None,
            "sample": det[:3]}
