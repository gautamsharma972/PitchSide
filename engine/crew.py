"""PitchSide Match Desk - a multi-agent system on Microsoft Agent Framework.

    Analyst  --brief-->  Storyteller  --draft-->  Verifier --REVISE--> Storyteller (max 2 loops) --> approved text
    (tools: match data)  (persona + language)     (tool: fact-check)

Three specialised agents with separate instructions and tools, orchestrated by `run_crew`. The Analyst is the only
agent allowed to touch match data (via tools), the Storyteller never sees raw data, and the Verifier gates every
output - so a hallucination is caught *by another agent*, then repaired. A server-side check re-verifies the final text.

Model backend: Microsoft Foundry (FoundryChatClient) when FOUNDRY_PROJECT_ENDPOINT is set; otherwise the same agents
run against the in-process scripted offline model (engine/offline_model.py), so everything works with no Azure."""
import json
import os
from typing import Dict, List, Optional

try:
    from agent_framework import Agent, tool
    from agent_framework.foundry import FoundryChatClient
    AVAILABLE = True
except ImportError:                                   # Agent Framework needs Python >= 3.10
    AVAILABLE = False

from . import narrate, offline_model
from .analytics import key_moments, match_facts
from .tools import run_tool

MAX_REVISIONS = 2

ANALYST = """ROLE:ANALYST
You are the Match Desk analyst. You are the ONLY agent allowed to read match data, and only through your tools.
Call tools for every figure; never guess. Return a compact JSON brief containing just the facts the task needs."""
STORYTELLER = """ROLE:STORYTELLER
You are the Match Desk storyteller for studio, broadcast and streaming. You never see raw data: write only from the
analyst's BRIEF_JSON. Match the requested PERSONA and LANGUAGE (keep player and club names unchanged), cite supporting
event ids as [#id], invent nothing. If FEEDBACK is present, fix exactly those problems."""
VERIFIER = """ROLE:VERIFIER
You are the Match Desk fact-checker. Call verify_claims on the DRAFT. Reply exactly 'APPROVED' if it passes,
otherwise 'REVISE: <the issues>'. Never rewrite the text yourself."""


class _OfflineCred:
    def get_token(self, *a, **k):
        from azure.core.credentials import AccessToken
        return AccessToken("offline", 9999999999)


def live_backend() -> bool:
    return bool(os.getenv("FOUNDRY_PROJECT_ENDPOINT"))


def _client():
    if live_backend():
        from azure.identity import DefaultAzureCredential
        return FoundryChatClient(project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], model=os.getenv("FOUNDRY_MODEL", "gpt-4o"),
                                 credential=DefaultAzureCredential())
    return FoundryChatClient(project_endpoint=offline_model.url(), model="offline-scripted", credential=_OfflineCred())


def _make_tools(match: Dict, trace: List[Dict], ctx: Dict):
    def log(agent, name, args):
        trace.append({"agent": agent, "action": "tool", "detail": "%s(%s)" % (name, ", ".join("%s=%s" % kv for kv in args.items() if kv[1] is not None))})

    @tool(name="get_match_state", description="Score, xG, shots, win probability, goals and cards up to a minute or event id (default: full time).")
    def get_match_state(minute: Optional[int] = None, event_id: Optional[int] = None) -> dict:
        log("analyst", "get_match_state", {"minute": minute, "event_id": event_id})
        return run_tool(match, "get_match_state", {"minute": minute, "event_id": event_id})

    @tool(name="get_key_moments", description="Most influential moments with numeric factors explaining why each mattered.")
    def get_key_moments(top: int = 6) -> dict:
        log("analyst", "get_key_moments", {"top": top})
        return run_tool(match, "get_key_moments", {"top": top})

    @tool(name="get_insights", description="Control vs chaos, pressing and momentum insights (and milestones) with their numeric evidence.")
    def get_insights(kind: Optional[str] = None) -> dict:
        log("analyst", "get_insights", {"kind": kind})
        return run_tool(match, "get_insights", {"kind": kind})

    @tool(name="get_player_stats", description="Performance card for a player (full or partial name).")
    def get_player_stats(player: str) -> dict:
        log("analyst", "get_player_stats", {"player": player})
        return run_tool(match, "get_player_stats", {"player": player})

    @tool(name="get_leaders", description="Top players for a metric: speed, distance, xg, shots, tackles, passes.")
    def get_leaders(metric: str) -> dict:
        log("analyst", "get_leaders", {"metric": metric})
        return run_tool(match, "get_leaders", {"metric": metric})

    @tool(name="verify_claims", description="Fact-check a draft against the match data: citations, scorelines and player names.")
    def verify_claims(text: str) -> dict:
        facts = match_facts(match, ctx.get("upto"))
        res = narrate.verify(text, facts, {e["id"] for e in match["events"]}, ctx["roster"])
        log("verifier", "verify_claims", {"ok": res["ok"]})
        return res

    return [get_match_state, get_key_moments, get_insights, get_player_stats, get_leaders], [verify_claims]


async def run_crew(match: Dict, task: Dict) -> Dict:
    """task: {task: moment|recap|ask, event_id?, question?, persona, lang, inject_error?}"""
    if not AVAILABLE:
        raise RuntimeError("Microsoft Agent Framework needs Python >= 3.10")
    kind, persona, lang = task["task"], task.get("persona", "broadcast"), task.get("lang", "en")
    trace: List[Dict] = []
    ctx = {"upto": task.get("event_id") if kind == "moment" else None,
           "roster": {p for t in match["teams"].values() for p in t["players"]}}
    analyst_tools, verifier_tools = _make_tools(match, trace, ctx)
    client = _client()
    a = Agent(client, ANALYST, name="analyst", tools=analyst_tools)
    s = Agent(client, STORYTELLER, name="storyteller")
    v = Agent(client, VERIFIER, name="verifier", tools=verifier_tools)

    payload = {k: task[k] for k in ("task", "event_id", "question") if task.get(k) is not None}
    payload["players"] = sorted(ctx["roster"])
    brief = (await a.run("TASK_JSON: " + json.dumps(payload))).text
    trace.append({"agent": "analyst", "action": "brief", "detail": brief[:160] + ("…" if len(brief) > 160 else "")})

    draft, verdict, revisions, feedback = "", "", 0, ""
    for attempt in range(MAX_REVISIONS + 1):
        prompt = "BRIEF_JSON: %s\nPERSONA: %s\nLANGUAGE: %s\nFEEDBACK: %s\nINJECT_ERROR: %d" % (
            brief, persona, lang, feedback, 1 if task.get("inject_error") else 0)
        draft = (await s.run(prompt)).text.strip()
        trace.append({"agent": "storyteller", "action": "draft" if attempt == 0 else "revision %d" % attempt, "detail": draft[:200]})
        verdict = (await v.run("DRAFT: " + draft)).text.strip()
        trace.append({"agent": "verifier", "action": "verdict", "detail": verdict[:200]})
        if verdict.upper().startswith("APPROVED"):
            break
        feedback, revisions = verdict.replace("REVISE:", "").strip(), revisions + 1

    facts = match_facts(match, ctx["upto"])
    check = narrate.verify(draft, facts, {e["id"] for e in match["events"]}, ctx["roster"])
    return {"text": draft, "approved": verdict.upper().startswith("APPROVED") and check["ok"], "revisions": revisions,
            "trace": trace, "check": check, "agents": ["analyst", "storyteller", "verifier"],
            "mode": "foundry-multi-agent" if live_backend() else "offline-scripted-model (Agent Framework agents)"}
