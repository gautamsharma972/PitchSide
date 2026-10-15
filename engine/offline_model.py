"""Scripted offline 'model' served over the OpenAI Responses protocol.

Why: lets the *real* Microsoft Agent Framework agents (tool-calling loop, orchestration) run end to end with no
Azure access - in dev, tests, CI and demos. It is NOT an LLM: each role follows a fixed policy and only ever uses
what its tools returned, so it can't invent facts. With FOUNDRY_PROJECT_ENDPOINT set, the same agents talk to a real
Foundry model instead and this module is unused."""
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import narrate

_srv = None
_lock = threading.Lock()
_state = {}          # response id -> conversation so far (Responses API is stateful via previous_response_id)


def url() -> str:
    """Start (once) the in-process server and return a Foundry-style project endpoint pointing at it."""
    global _srv
    with _lock:
        if _srv is None:
            _srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
            threading.Thread(target=_srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/api/projects/offline" % _srv.server_address[1]


def _id(p): return "%s_%s" % (p, uuid.uuid4().hex[:12])


def _fc(name, args):
    return {"type": "function_call", "id": _id("fc"), "call_id": _id("call"), "name": name,
            "arguments": json.dumps(args), "status": "completed"}


def _msg(text):
    return {"type": "message", "id": _id("msg"), "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _marker(text, key):
    m = re.search(r"^%s: ?(.*)$" % key, text, re.M)
    return m.group(1).strip() if m else ""


def _json_after(text, key):
    i = text.find(key + ":")
    if i < 0:
        return {}
    dec = json.JSONDecoder()
    try:
        return dec.raw_decode(text[i + len(key) + 1:].lstrip())[0]
    except ValueError:
        return {}


# ------------------------------------------------------------------ role policies
def analyst(user, outputs):
    task = _json_after(user, "TASK_JSON")
    kind = task.get("task")
    if not outputs:
        if kind == "moment":
            return [_fc("get_match_state", {"event_id": task["event_id"]}), _fc("get_key_moments", {"top": 40}),
                    _fc("get_insights", {"kind": "insight"})]
        if kind == "recap":
            return [_fc("get_match_state", {}), _fc("get_key_moments", {"top": 6}), _fc("get_insights", {"kind": "insight"})]
        q = task.get("question", "").lower()
        if any(w in q for w in ("fastest", "speed", "sprint")): return [_fc("get_leaders", {"metric": "speed"})]
        if any(w in q for w in ("ran", "run ", "distance", "covered")): return [_fc("get_leaders", {"metric": "distance"})]
        if any(w in q for w in ("control", "chaos", "press", "rhythm", "tempo")): return [_fc("get_insights", {})]
        for name in task.get("players", []):
            if name.lower() in q or name.split()[-1].lower() in q: return [_fc("get_player_stats", {"player": name})]
        return [_fc("get_match_state", {})]
    by = {o["name"]: o["data"] for o in outputs}
    brief = {"task": kind}
    if kind == "moment":
        brief["facts"] = by.get("get_match_state", {})
        brief["moment"] = next((m for m in by.get("get_key_moments", {}).get("moments", []) if m["event_id"] == task["event_id"]), None)
    elif kind == "recap":
        brief["facts"] = by.get("get_match_state", {})
        brief["moments"] = by.get("get_key_moments", {}).get("moments", [])
        brief["insights"] = by.get("get_insights", {}).get("signals", [])
    else:
        d = next(iter(by.values()), {})
        if "leaders" in d and d["leaders"]:
            l = d["leaders"][0]
            brief["answer"] = "%s leads %s with %s %s." % (l["player"], d["metric"], l["value"], l["unit"])
        elif "signals" in d:
            brief["answer"] = " ".join("%s: %s (%s)" % (s["clock"], s["key"], s.get("evidence") or "") for s in d["signals"][:3]) or "No insights yet."
        elif "name" in d:
            brief["answer"] = ("%(name)s: %(passes)s passes (%(pass_acc)s%% accuracy), %(shots)s shots, %(xg)s xG, %(goals)s goals, "
                               "%(distance_km)s km, top speed %(top_speed)s km/h.") % d
        elif "score" in d:
            brief["answer"] = "%s %d-%d %s (xG %.2f-%.2f)." % (d["home"], d["score"]["home"], d["score"]["away"], d["away"], d["xg"]["home"], d["xg"]["away"])
        else:
            brief["answer"] = d.get("error", "I don't have data for that.")
    return json.dumps(brief)


def storyteller(user, outputs):
    brief, persona, lang = _json_after(user, "BRIEF_JSON"), _marker(user, "PERSONA") or "broadcast", _marker(user, "LANGUAGE") or "en"
    feedback, inject = _marker(user, "FEEDBACK"), _marker(user, "INJECT_ERROR") == "1"
    kind = brief.get("task")
    if kind == "moment" and brief.get("moment"):
        text = narrate.template_moment(brief["moment"], brief["facts"], persona, lang)
    elif kind == "recap":
        text = narrate.template_recap(brief["facts"], brief.get("moments", []), lang)
    else:
        text = brief.get("answer", "")
    if inject and not feedback:      # demo/test hook: simulate an LLM hallucination so the verifier has something to catch
        text += " Zlatko Novak also scored a late winner for the home side [#9999]."
    return text


def verifier(user, outputs):
    draft = user.split("DRAFT:", 1)[-1].strip()
    if not outputs:
        return [_fc("verify_claims", {"text": draft})]
    res = outputs[0]["data"]
    return "APPROVED" if res.get("ok") else "REVISE: " + "; ".join(res.get("issues", []))


ROLES = {"ROLE:ANALYST": analyst, "ROLE:STORYTELLER": storyteller, "ROLE:VERIFIER": verifier}


def respond(body: dict) -> dict:
    instr = body.get("instructions") or ""
    fn = next((f for k, f in ROLES.items() if k in instr), None)
    items = body.get("input") or []
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    user, outputs, calls = "", [], {}
    prev = _state.get(body.get("previous_response_id") or "")
    if prev:
        user, calls = prev["user"], dict(prev["calls"])
    for it in items:
        t = it.get("type")
        if t == "message" and it.get("role") == "user":
            c = it.get("content")
            user += (c if isinstance(c, str) else " ".join(x.get("text", "") for x in c)) + "\n"
        elif t == "function_call":
            calls[it["call_id"]] = it["name"]
        elif t == "function_call_output":
            try: data = json.loads(it["output"])
            except Exception: data = {"raw": it["output"]}
            outputs.append({"name": calls.get(it["call_id"], ""), "data": data})
    out = fn(user, outputs) if fn else "offline model: unknown role"
    output = out if isinstance(out, list) else [_msg(out)]
    rid = _id("resp")
    for o in output:
        if o["type"] == "function_call":
            calls[o["call_id"]] = o["name"]
    _state[rid] = {"user": user, "calls": calls}
    if len(_state) > 500:
        _state.pop(next(iter(_state)))
    return {"id": rid, "object": "response", "created_at": int(time.time()), "status": "completed",
            "model": body.get("model", "offline-scripted"), "output": output, "parallel_tool_calls": True,
            "tool_choice": "auto", "tools": [], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                                          "input_tokens_details": {"cached_tokens": 0},
                                                          "output_tokens_details": {"reasoning_tokens": 0}}}


class _H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        data = json.dumps(respond(body)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass
