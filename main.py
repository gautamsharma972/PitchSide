import asyncio
import os
from typing import Dict, List

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from engine import agent, autoevent, dataset, fabric, generate_match, i18n, key_moments, load_match, match_facts
from engine import narrate, personalize, store, streaming
from engine import crew
from engine.hub import LiveHub, sse
from engine.live import LiveEngine

app = FastAPI(title="PitchSide - explainable, real-time match intelligence")
_cache: Dict[int, Dict] = {}
hub = LiveHub()
HERE = os.path.dirname(__file__)
DEMO_SEED = 146      # chosen for story: 3-1 home win, a brace, a chaos spell, control + pressing phases, distinct top speeds


def get_match(seed: int):
    if seed not in _cache:
        _cache[seed] = load_match(seed)
    return _cache[seed]


def _roster(m):
    return {p for t in m["teams"].values() for p in t["players"]}


def _check_style(persona: str, lang: str):
    if persona not in narrate.PERSONAS or lang not in i18n.LANGS:
        raise HTTPException(400, "bad persona/lang")


# ------------------------------------------------------------------ status / batch views
@app.get("/api/status")
def status():
    return {"llm": narrate.llm_available(), "agent": agent.available(), "eventhub": streaming.enabled(),
            "fabric": bool(os.getenv("FABRIC_WORKSPACE")), "languages": i18n.LANGS, "personas": list(narrate.PERSONAS),
            "crew": crew.AVAILABLE, "crew_backend": "foundry" if crew.AVAILABLE and crew.live_backend() else "offline-scripted", "demo_seed": DEMO_SEED, "modes": personalize.MODES, "metrics": personalize.METRICS, "live": hub.health()}


def _players(m):
    return sorted((m["engine"].player_card(n) for n in m["engine"].players),
                  key=lambda c: (c["team"] != "home", -(c["distance_km"] or 0)))


@app.get("/api/match")
def match(seed: int = DEMO_SEED):
    """Whole-match summary for the dashboard (per-minute timeline, shots, key moments, players)."""
    m = get_match(seed)
    tl, last_min = [], -1
    for e in m["events"]:
        mi = int(e["elapsed"] // 60)
        if mi != last_min or e["type"] in ("goal", "red_card"):
            tl.append(dict(e["state"], clock=e["clock"], id=e["id"]))
            last_min = mi
    shots = [{k: e[k] for k in ("id", "clock", "team", "player", "type", "x", "y", "xg", "shot_speed")}
             for e in m["events"] if e["type"].startswith("shot") or e["type"] == "goal"]
    return {"teams": m["teams"], "meta": m["meta"], "timeline": tl, "shots": shots, "moments": key_moments(m),
            "final": m["engine"].snapshot(), "players": _players(m)}


@app.get("/api/players")
def players(seed: int = DEMO_SEED):
    return _players(get_match(seed))


@app.get("/api/moment/{event_id}")
def moment(event_id: int, seed: int = DEMO_SEED, persona: str = "broadcast", lang: str = "en"):
    _check_style(persona, lang)
    m = get_match(seed)
    mom = next((x for x in key_moments(m, top=60) if x["event_id"] == event_id), None)
    if not mom:
        raise HTTPException(404, "not a key moment")
    k = store.key("moment", seed, event_id, persona, lang, narrate.llm_available())
    if hit := store.get(k):
        return hit
    facts = match_facts(m, event_id)
    text, mode = narrate.narrate_moment(mom, facts, persona, lang)
    ids = {e["id"] for e in m["events"]}
    out = {"text": text, "mode": mode, "moment": mom, "check": narrate.verify(text, facts, ids, _roster(m))}
    if "error" not in mode:
        store.put(k, out)
    return out


@app.get("/api/recap")
def recap(seed: int = DEMO_SEED, persona: str = "broadcast", lang: str = "en"):
    _check_style(persona, lang)
    m = get_match(seed)
    k = store.key("recap", seed, persona, lang, narrate.llm_available())
    if hit := store.get(k):
        return hit
    facts, moments = match_facts(m), key_moments(m)
    text, mode = narrate.narrate_recap(facts, moments, persona, lang)
    ids = {e["id"] for e in m["events"]}
    out = {"text": text, "mode": mode, "facts": facts, "check": narrate.verify(text, facts, ids, _roster(m))}
    if "error" not in mode:
        store.put(k, out)
    return out


# ------------------------------------------------------------------ real-time pipeline
@app.post("/api/feed/start")
async def feed_start(seed: int = DEMO_SEED, speed: float = 60.0):
    """(Re)start the live feed: paced replay of a synthetic match through the real-time engine."""
    raw = generate_match(seed)
    hub.reset(raw["teams"], raw["meta"], seed)
    hub.task = asyncio.create_task(hub.run_replay(raw["events"], speed))
    return {"started": True, "seed": seed, "speed": speed, "events": len(raw["events"]), "teams": raw["teams"], "meta": raw["meta"]}


@app.post("/api/feed/reset")
async def feed_reset():
    """Empty live hub for an external feed - POST events to /api/ingest."""
    hub.reset(None, None)
    return {"reset": True, "teams": hub.teams}


_EVENT_TYPES = {"pass", "shot_saved", "shot_blocked", "shot_off_target", "goal", "tackle", "interception", "pressure",
                "possession_change", "sprint", "foul", "yellow_card", "red_card", "corner", "substitution", "kickoff",
                "half_time", "full_time"}
_DEFAULTS = {"player": "", "elapsed": 0.0, "clock": "", "minute": 0, "x": None, "y": None, "to_x": None, "to_y": None,
             "to_player": "", "success": None, "distance_m": None, "difficulty": None, "ball_speed": None,
             "shot_speed": None, "player_speed": None, "pressure": 0, "xg": 0.0, "detail": ""}


@app.post("/api/ingest")
async def ingest(body: Dict = Body(...)):
    """Push one event (or {"events": [...]}) from any source. Minimum: type, team, player, elapsed (s)."""
    evs: List[Dict] = body.get("events") or [body]
    out = []
    for raw in evs:
        if raw.get("type") not in _EVENT_TYPES or raw.get("team") not in ("home", "away"):
            raise HTTPException(422, "event needs a valid type and team (home|away)")
        e = {**_DEFAULTS, **raw}
        e["id"] = raw.get("id") or (len(hub.match["events"]) + 1)
        e.setdefault("poss", e["team"])
        f = hub.push(e)
        out.append({"id": e["id"], "latency_ms": f["latency_ms"], "signals": [s["key"] for s in f["signals"]]})
    return {"ingested": len(out), "results": out}


@app.get("/api/live")
async def live(request: Request, lang: str = "en", mode: str = "analyst", team: str = None, players: str = "",
               focus: str = None, metric: str = "none"):
    """SSE stream of personalised frames: feed items, overlays (machine-readable), ticker, player card."""
    prof = personalize.parse_profile(lang, mode, team, players, focus, metric)
    sub = hub.subscribe(prof)

    async def gen():
        try:
            yield sse("hello", {"profile": prof, "teams": hub.teams, "meta": hub.meta, "health": hub.health()})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield sse(msg["kind"], msg["data"])
                if msg["kind"] == "end":
                    break
        finally:
            hub.unsubscribe(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/overlays")
def overlays(seed: int = DEMO_SEED, lang: str = "en", mode: str = "analyst", team: str = None, players: str = "",
             focus: str = None, metric: str = "none", min_priority: int = 1):
    """Complete timed overlay track for a match (JSON) - what a graphics renderer consumes alongside the video."""
    prof = personalize.parse_profile(lang, mode, team, players, focus, metric)
    raw = generate_match(seed)
    eng = LiveEngine(raw["teams"], raw["meta"])
    track = []
    for e in raw["events"]:
        r = personalize.render(eng.ingest(e), prof, eng, raw["teams"])
        track.extend(o for o in r["overlays"] if o["priority"] >= min_priority)
    return {"profile": prof, "match": {"seed": seed, "teams": {k: v["name"] for k, v in raw["teams"].items()}},
            "schema": "overlay: {id, type, t_start_s, duration_ms, priority, payload}", "count": len(track), "overlays": track}


@app.get("/api/health")
def health():
    return hub.health()


# ------------------------------------------------------------------ auto-eventing, data, export, agent
@app.get("/api/autoevent")
def autoevent_eval(seed: int = DEMO_SEED, start: float = 600, end: float = 900):
    """Tracking-to-events detector evaluated against ground truth (stand-in for a video model)."""
    return autoevent.evaluate(get_match(seed), start, end)


@app.post("/api/dataset")
def make_dataset(n: int = 10, seed: int = 100):
    if not 1 <= n <= 200:
        raise HTTPException(400, "n must be 1-200")
    return dataset.generate_dataset(n, seed, os.path.join(HERE, "export", "dataset"))


@app.post("/api/export")
def export(seed: int = DEMO_SEED):
    """Export the match to a Fabric Lakehouse (OneLake) - or ./export when Fabric isn't configured."""
    return fabric.export(get_match(seed), seed)


@app.post("/api/agent")
def agent_ask(seed: int = DEMO_SEED, body: dict = Body(...)):
    q = (body.get("question") or "").strip()[:500]
    if not q:
        raise HTTPException(400, "question required")
    return agent.ask(get_match(seed), q, body.get("previous_response_id"))


@app.post("/api/crew")
async def crew_run(seed: int = DEMO_SEED, body: dict = Body(...)):
    """Multi-agent Match Desk (Agent Framework): Analyst -> Storyteller -> Verifier, with a revise loop."""
    if not crew.AVAILABLE:
        raise HTTPException(501, "Microsoft Agent Framework needs Python >= 3.10")
    kind = body.get("task")
    persona, lang = body.get("persona", "broadcast"), body.get("lang", "en")
    _check_style(persona, lang)
    m = get_match(seed)
    task = {"task": kind, "persona": persona, "lang": lang, "inject_error": bool(body.get("inject_error"))}
    if kind == "moment":
        if not any(x["event_id"] == body.get("event_id") for x in key_moments(m, top=60)):
            raise HTTPException(404, "not a key moment")
        task["event_id"] = int(body["event_id"])
    elif kind == "ask":
        task["question"] = (body.get("question") or "").strip()[:500]
        if not task["question"]:
            raise HTTPException(400, "question required")
    elif kind != "recap":
        raise HTTPException(400, "task must be moment | recap | ask")
    return await crew.run_crew(m, task)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
