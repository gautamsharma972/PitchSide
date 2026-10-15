"""Live hub: one match feed -> many personalised, real-time subscribers (SSE).

Feeds: a paced replay of a generated/official match, or external events POSTed to /api/ingest. Every event goes
through LiveEngine once; each subscriber then gets its own rendering (language, mode, favourites, focus)."""
import asyncio
import json
import time
from collections import deque
from typing import Dict, List, Optional

from . import narrate, personalize, store, streaming
from .analytics import build_moment, match_facts
from .live import LiveEngine


class Sub:
    def __init__(self, profile):
        self.profile = profile
        self.q: asyncio.Queue = asyncio.Queue(maxsize=400)

    def put(self, msg: Dict):
        try:
            self.q.put_nowait(msg)
        except asyncio.QueueFull:                          # slow client: drop oldest, never block the feed
            try:
                self.q.get_nowait()
                self.q.put_nowait(msg)
            except Exception:
                pass


class LiveHub:
    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.reset(None, None)

    def reset(self, teams: Optional[Dict], meta: Optional[Dict], seed: int = 0):
        if self.task and not self.task.done():
            self.task.cancel()
        if teams is None:
            from .simulate import TEAMS
            teams = {k: dict(v, style="live") for k, v in TEAMS.items()}
        self.seed, self.teams, self.meta = seed, teams, meta or {}
        self.engine = LiveEngine(teams, meta)
        self.match = {"teams": teams, "events": [], "meta": meta or {}}
        self.subs: List[Sub] = []
        self.recent = deque(maxlen=8)
        self.running, self.done = False, False
        self.started = None
        self.stats = {"events": 0, "lat_sum": 0.0, "lat_max": 0.0}

    # ---- producers ----
    def push(self, e: Dict) -> Dict:
        frame = self.engine.ingest(e)
        self.match["events"].append(e)
        self.stats["events"] += 1
        self.recent.extend(sg for sg in frame["signals"] if sg["priority"] >= 3)
        self.stats["lat_sum"] += frame["latency_ms"]
        self.stats["lat_max"] = max(self.stats["lat_max"], frame["latency_ms"])
        for s in list(self.subs):
            s.put({"kind": "frame", "data": personalize.render(frame, s.profile, self.engine, self.teams)})
        if any(sg["priority"] >= 5 and sg["kind"] == "story" for sg in frame["signals"]) and narrate.llm_available():
            for s in list(self.subs):
                asyncio.ensure_future(self._upgrade(frame, s, len(self.match["events"]) - 1))
        return frame

    async def _upgrade(self, frame, sub: Sub, idx: int):
        """Replace template commentary on major moments with Azure OpenAI prose (verified before sending)."""
        try:
            evs = self.match["events"]
            moment = build_moment(evs, idx)
            facts = match_facts({"teams": self.teams, "events": evs}, evs[idx]["id"])
            k = store.key("live", self.seed, evs[idx]["id"], sub.profile["mode"], sub.profile["lang"])
            hit = store.get(k)
            if not hit:
                persona = "studio" if sub.profile["mode"] == "analyst" else "fan"
                text, mode = await asyncio.to_thread(narrate.narrate_moment, moment, facts, persona, sub.profile["lang"])
                ids = {x["id"] for x in evs}
                roster = {p for t in self.teams.values() for p in t["players"]}
                hit = {"text": text, "mode": mode, "check": narrate.verify(text, facts, ids, roster)}
                if "error" not in mode:
                    store.put(k, hit)
            if hit["check"]["ok"]:
                sub.put({"kind": "narration", "data": dict(hit, event_id=evs[idx]["id"])})
        except Exception:
            pass

    async def run_replay(self, raw_events: List[Dict], speed: float):
        self.running, self.done, self.started = True, False, time.time()
        prev = 0.0
        publish = streaming.enabled()
        try:
            for e in raw_events:
                gap = max(0.0, e["elapsed"] - prev) / max(speed, 1.0)
                prev = e["elapsed"]
                await asyncio.sleep(min(gap, 8.0))
                self.push(e)
                if publish:
                    asyncio.get_running_loop().run_in_executor(None, streaming.publish, self.match["events"][-1], self.seed)
            self.done = True
            for s in list(self.subs):
                s.put({"kind": "end", "data": {}})
        finally:
            self.running = False

    # ---- consumers ----
    def subscribe(self, profile: Dict) -> Sub:
        sub = Sub(profile)
        if self.engine.last_frame:                          # late joiner: current state + recent moments, no history dump
            fr = dict(self.engine.last_frame)
            fr["signals"] = list(self.recent)
            sub.put({"kind": "frame", "data": personalize.render(fr, profile, self.engine, self.teams)})
        self.subs.append(sub)
        if self.done:
            sub.put({"kind": "end", "data": {}})
        return sub

    def unsubscribe(self, sub: Sub):
        if sub in self.subs:
            self.subs.remove(sub)

    def health(self):
        n = self.stats["events"]
        return {"running": self.running, "done": self.done, "events": n, "subscribers": len(self.subs),
                "latency_ms_avg": round(self.stats["lat_sum"] / n, 3) if n else 0, "latency_ms_max": round(self.stats["lat_max"], 3)}


def sse(kind: str, data: Dict) -> str:
    return "event: %s\ndata: %s\n\n" % (kind, json.dumps(data, ensure_ascii=False))
