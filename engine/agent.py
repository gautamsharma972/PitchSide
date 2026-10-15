"""Microsoft Foundry Agent Service integration (azure-ai-projects v2).

`ensure_agent()` registers a versioned prompt agent with our analytics function tools; `ask()` runs the
Responses loop, executing tool calls locally until the agent produces a final grounded answer."""
import json
import os

from .tools import TOOL_SPECS, offline_answer, run_tool

AGENT_NAME = "pitchside-analyst"
INSTRUCTIONS = """You are PitchSide Analyst, a football match-intelligence agent for studios, broadcasters and fans.
Answer ONLY using results from your tools - call them for every numeric or factual claim; never guess.
Cite supporting event ids as [#id]. If the data does not contain the answer, say so.
Use get_player_stats for player questions and get_insights for control/chaos/pressing explanations.
Explain WHY moments mattered using the factors returned (xG, momentum shift, win-probability swing).
Reply in the user's language and keep answers under 120 words."""

_project = None
_ready = False


def available() -> bool:
    return bool(os.getenv("FOUNDRY_PROJECT_ENDPOINT"))


def _client():
    global _project
    if _project is None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        _project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                                   credential=DefaultAzureCredential())
    return _project


def ensure_agent() -> None:
    global _ready
    if _ready:
        return
    from azure.ai.projects.models import PromptAgentDefinition
    _client().agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(model=os.getenv("FOUNDRY_MODEL", "gpt-4o"),
                                         instructions=INSTRUCTIONS, tools=TOOL_SPECS))
    _ready = True


def ask(match: dict, question: str, previous_response_id: str = None, max_steps: int = 6) -> dict:
    if not available():
        return {"answer": offline_answer(match, question), "mode": "offline-tools", "response_id": None, "tool_calls": []}
    try:
        ensure_agent()
        oai = _client().get_openai_client()
        ref = {"agent_reference": {"name": AGENT_NAME, "type": "agent_reference"}}
        resp = oai.responses.create(input=question, previous_response_id=previous_response_id, extra_body=ref)
        calls = []
        for _ in range(max_steps):
            fcs = [i for i in resp.output if i.type == "function_call"]
            if not fcs:
                break
            outputs = []
            for fc in fcs:
                args = json.loads(fc.arguments or "{}")
                calls.append({"tool": fc.name, "args": args})
                outputs.append({"type": "function_call_output", "call_id": fc.call_id,
                                "output": json.dumps(run_tool(match, fc.name, args))})
            resp = oai.responses.create(input=outputs, previous_response_id=resp.id, extra_body=ref)
        return {"answer": resp.output_text, "mode": "foundry-agent", "response_id": resp.id, "tool_calls": calls}
    except Exception as ex:
        return {"answer": offline_answer(match, question), "mode": "offline-tools (agent error: %s)" % type(ex).__name__,
                "response_id": None, "tool_calls": []}
