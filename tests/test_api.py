import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi.testclient import TestClient

import main
from engine import generate_match, i18n, load_match, personalize
from engine.live import LiveEngine

c = TestClient(main.app)


def frames_for(seed=3, **prof):
    raw = generate_match(seed)
    eng = LiveEngine(raw["teams"], raw["meta"])
    p = personalize.parse_profile(**prof)
    return raw, eng, [personalize.render(eng.ingest(e), p, eng, raw["teams"]) for e in raw["events"]]


def test_dataset_realism():
    for seed in (1, 2, 3):
        ev = generate_match(seed)["events"]
        types = {e["type"] for e in ev}
        assert {"pass", "tackle", "pressure", "possession_change", "interception", "sprint"} <= types
        assert all(ev[i]["elapsed"] <= ev[i + 1]["elapsed"] for i in range(len(ev) - 1))
        passes = [e for e in ev if e["type"] == "pass"]
        assert 700 < len(passes) < 1400
        assert 0.7 < sum(e["success"] for e in passes) / len(passes) < 0.93
        assert all(0 < e["difficulty"] < 1 and e["ball_speed"] > 0 for e in passes)
        assert generate_match(seed)["events"] == ev            # deterministic


def test_batch_match_and_moments():
    r = c.get("/api/match").json()
    assert r["moments"] and r["timeline"] and r["players"]
    assert abs(sum(r["final"]["wp"].values()) - 1) < 1e-2


def test_batch_equals_live():
    """Batch analytics and the live engine must agree (same code path)."""
    m = load_match(3)
    raw, eng, _ = frames_for(3)
    assert eng.score == m["engine"].score and abs(eng.xg["home"] - m["engine"].xg["home"]) < 1e-9


def test_engine_latency_budget():
    raw, eng, fr = frames_for(3)
    lats = [f["latency_ms"] for f in fr]
    assert sum(lats) / len(lats) < 2.0          # ms per event, whole pipeline incl. personalization excluded


def test_explainability_and_milestones():
    m = load_match(3)
    kinds = {(s["kind"], s["key"]) for s in m["signals"]}
    assert ("insight", "control") in kinds or ("insight", "press") in kinds
    ins = next(s for s in m["signals"] if s["kind"] == "insight")
    assert ins["evidence"]                        # every insight carries its numbers
    assert any(s["key"] == "distance" for s in m["signals"])


def test_all_languages_render_goal():
    for lang in i18n.LANGS:
        raw, eng, fr = frames_for(3, lang=lang)
        goal = next(item for f in fr for item in f["feed"] if item["key"] == "goal")
        assert goal["text"] and "{" not in goal["text"]
        if lang != "en":
            assert goal["text"] != next(i for f in frames_for(3, lang="en")[2] for i in f["feed"] if i["key"] == "goal")["text"]


def test_personalization_modes_differ():
    _, _, analyst = frames_for(3, mode="analyst")
    _, _, fan = frames_for(3, mode="fan")
    a = sum(len(f["feed"]) + len(f["overlays"]) for f in analyst)
    b = sum(len(f["feed"]) + len(f["overlays"]) for f in fan)
    assert b < a * 0.6
    assert not any(o["type"] == "pass_card" for f in fan for o in f["overlays"])
    assert any(o["type"] == "pass_card" for f in analyst for o in f["overlays"])


def test_favourite_team_and_focus_player():
    raw, eng, fr = frames_for(3, team="away", mode="fan")
    mine = [i for f in fr for i in f["feed"] if i["mine"] == "for"]
    assert mine and all(i["text"].startswith("⭐") for i in mine)
    name = raw["teams"]["home"]["players"][9]
    _, _, fr = frames_for(3, focus=name)
    assert fr[-1]["player_card"]["name"] == name
    tags = [o["payload"]["player"] for f in fr for o in f["overlays"] if o["type"] == "player_tag"]
    assert tags and set(tags) == {name}


def test_metric_ticker():
    _, _, fr = frames_for(3, metric="speed")
    assert fr[-1]["ticker"]["leaders"][0]["unit"] == "km/h"


def test_overlay_track_machine_readable():
    r = c.get("/api/overlays?mode=analyst&lang=es").json()
    assert r["count"] > 100
    o = r["overlays"][0]
    assert {"id", "type", "t_start_s", "duration_ms", "priority", "payload"} <= set(o)
    assert any(x["type"] == "lower_third" and x["payload"]["text"] for x in r["overlays"])


def test_live_sse_end_to_end():
    with TestClient(main.app) as cl:
        assert cl.post("/api/feed/start?seed=3&speed=100000").json()["started"]
        got = {"frame": 0, "hello": 0, "end": 0}
        with cl.stream("GET", "/api/live?lang=fr&mode=fan") as r:
            ev = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                    got[ev] = got.get(ev, 0) + 1
                    if ev == "end":
                        break
        assert got["hello"] == 1 and got["end"] == 1 and got["frame"] > 100
        assert cl.get("/api/health").json()["events"] > 1500


def test_ingest_external_feed():
    with TestClient(main.app) as cl:
        cl.post("/api/feed/reset")
        r = cl.post("/api/ingest", json={"type": "goal", "team": "home", "player": "Kane Whitlock", "elapsed": 600,
                                         "clock": "10'", "minute": 10, "xg": 0.4, "x": 90, "y": 50, "shot_speed": 110})
        assert r.json()["results"][0]["signals"][:1] == ["goal"]
        assert cl.post("/api/ingest", json={"type": "nope", "team": "home"}).status_code == 422


def test_moment_recap_grounded_and_agent():
    m = c.get("/api/match").json()["moments"][0]
    assert c.get("/api/moment/%d" % m["event_id"]).json()["check"]["ok"]
    assert c.get("/api/recap").json()["check"]["ok"]
    r = c.post("/api/agent", json={"question": "who was the fastest player?"}).json()
    assert "km/h" in r["answer"]


def test_autoevent_and_dataset(tmp_path):
    r = c.get("/api/autoevent").json()
    assert r["recall"] > 0.95 and r["distance_mae_m"] < 1.5
    d = c.post("/api/dataset?n=3&seed=500").json()
    assert d["matches"] == 3 and d["events"] > 4000


def test_export_local():
    r = c.post("/api/export").json()
    assert r["target"] == "local" and {"events.csv", "players.csv"} <= set(r["files"])


def test_offline_narration_and_recap_translate_and_ground():
    gid = next(m for m in c.get("/api/match").json()["moments"] if m["type"] == "goal")["event_id"]
    words = {"es": "GOL", "fr": "BUT", "de": "TOR", "pt": "GOLO", "ar": "هدف", "hi": "गोल", "ja": "ゴール", "zh": "进球"}
    en = c.get("/api/moment/%d?lang=en" % gid).json()["text"]
    for lang, w in words.items():
        r = c.get("/api/moment/%d?lang=%s&persona=studio" % (gid, lang)).json()
        assert w in r["text"] and r["text"] != en and r["check"]["ok"], (lang, r["text"], r["check"])
        rc = c.get("/api/recap?lang=%s" % lang).json()
        assert rc["check"]["ok"] and "translation needs" not in rc["text"]


import pytest
from engine import crew as crew_mod

needs_af = pytest.mark.skipif(not crew_mod.AVAILABLE, reason="Agent Framework needs Python >= 3.10")


@needs_af
def test_crew_moment_all_agents_and_tools():
    gid = next(m for m in c.get("/api/match").json()["moments"] if m["type"] == "goal")["event_id"]
    r = c.post("/api/crew", json={"task": "moment", "event_id": gid, "lang": "fr", "persona": "studio"}).json()
    assert r["approved"] and r["check"]["ok"] and r["revisions"] == 0 and "BUT" in r["text"]
    assert {t["agent"] for t in r["trace"]} == {"analyst", "storyteller", "verifier"}
    assert any(t["action"] == "tool" and "get_match_state" in t["detail"] for t in r["trace"])


@needs_af
def test_crew_verifier_catches_hallucination_and_storyteller_repairs():
    gid = next(m for m in c.get("/api/match").json()["moments"] if m["type"] == "goal")["event_id"]
    r = c.post("/api/crew", json={"task": "moment", "event_id": gid, "inject_error": True}).json()
    verdicts = [t["detail"] for t in r["trace"] if t["action"] == "verdict"]
    assert verdicts[0].startswith("REVISE") and verdicts[-1] == "APPROVED"
    assert r["revisions"] == 1 and r["approved"] and "Zlatko" not in r["text"]


@needs_af
def test_crew_recap_and_ask():
    r = c.post("/api/crew", json={"task": "recap", "lang": "ja"}).json()
    assert r["approved"] and "ゴール" in r["text"]
    r = c.post("/api/crew", json={"task": "ask", "question": "who ran the most?"}).json()
    assert "km" in r["text"] and r["approved"]
    assert c.post("/api/crew", json={"task": "moment", "event_id": 1}).status_code == 404
    assert c.post("/api/crew", json={"task": "bogus"}).status_code == 400
