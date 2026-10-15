"""Narrative layer: Azure OpenAI (Microsoft Foundry deployment) with an offline
template fallback, plus a grounding verifier that fact-checks every output
against the event data before it reaches the screen."""
import json
import os
import re
from typing import Dict, List

PERSONAS = {
    "broadcast": "an energetic live TV commentator: vivid, present tense, short punchy sentences",
    "studio": "a calm studio analyst: tactical, precise, explains WHY it mattered using the numbers",
    "fan": "a warm fan-focused storyteller: accessible, emotional, no jargon",
}
from . import i18n  # noqa: E402
from .i18n import LANGS  # noqa: E402

_client = None


def llm_available() -> bool:
    return bool(os.getenv("AZURE_OPENAI_ENDPOINT") and (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_USE_ENTRA")))


def _get_client():
    global _client
    if _client is None:
        from openai import AzureOpenAI
        kw = dict(azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                  api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
        if os.getenv("AZURE_OPENAI_API_KEY"):
            kw["api_key"] = os.environ["AZURE_OPENAI_API_KEY"]
        else:  # keyless auth via Entra ID
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            kw["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        _client = AzureOpenAI(**kw)
    return _client


SYSTEM = """You write football narration for {persona}.
STRICT RULES:
- Use ONLY facts from the provided JSON. Never invent players, scores, minutes, stats or events.
- Every sentence must be supported by facts; cite supporting event ids as [#id] right after the claim.
- Write in {language}. Keep player and team names unchanged. Keep [#id] citations as-is.
- Length: {length}."""


def _complete(system: str, user: str, max_tokens: int = 500) -> str:
    r = _get_client().chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.6, max_tokens=max_tokens)
    return r.choices[0].message.content.strip()


# ---------- offline fallback (deterministic, grounded by construction) ----------
def _tname(facts, team): return facts["home"] if team == "home" else facts["away"]


def template_moment(moment: Dict, facts: Dict, persona: str, lang: str = "en") -> str:
    """Offline narration, localised through the built-in phrasebook (names/clubs untouched)."""
    P = dict(player=moment["player"], team=_tname(facts, moment["team"]), clock=moment.get("clock", "%d'" % moment["minute"]),
             home=facts["home"], away=facts["away"], hs=moment["score"]["home"], xg=next(
                 (f["value"] for f in moment["factors"] if f["factor"] in ("shot_quality", "big_chance")), 0.0))
    P["as"] = moment["score"]["away"]
    key = {"goal": "goal", "red_card": "red", "shot_saved": "chance", "shot_blocked": "shot_block"}.get(moment["type"], "shot_off")
    text = i18n.render(key, P, lang) + " [#%d]" % moment["event_id"]
    if persona == "studio" and moment["type"] == "goal":
        text += " xG %.2f." % float(P["xg"])
    return text


def template_recap(facts: Dict, moments: List[Dict], lang: str = "en") -> str:
    s = facts["score"]
    out = [i18n.render("recap_head", dict(home=facts["home"], away=facts["away"], hs=s["home"], **{"as": s["away"]}), lang)]
    for g in facts["goals"]:
        out.append(i18n.render("recap_goal", dict(player=g["player"], team=_tname(facts, g["team"]),
                                                  clock="%d'" % g["minute"]), lang) + " [#%d]" % g["id"])
    if facts.get("possession_home_pct") is not None:
        out.append(i18n.render("recap_poss", dict(h=round(facts["possession_home_pct"]), a=round(100 - facts["possession_home_pct"])), lang))
    out.append(i18n.render("recap_xg", dict(h="%.2f" % facts["xg"]["home"], a="%.2f" % facts["xg"]["away"]), lang))
    if facts.get("top_speed_kmh"):
        t = facts["top_speed_kmh"][0]
        out.append(i18n.render("recap_fast", dict(player=t["player"], speed="%.1f" % t["value"]), lang))
    return " ".join(out)


# ---------- grounding verifier ----------
def verify(text: str, facts: Dict, valid_ids: set, roster: set) -> Dict:
    """Check citations exist, scorelines are real, and capitalised name pairs are known."""
    issues = []
    cited = [int(x) for x in re.findall(r"\[#(\d+)\]", text)]
    for c in cited:
        if c not in valid_ids: issues.append("unknown event id #%d" % c)
    real_scores = {"%d-%d" % (facts["score"]["home"], facts["score"]["away"])}
    for g in facts["goals"]:
        pass
    for m in re.finditer(r"\b(\d)\s*[-–]\s*(\d)\b", text):
        sc = "%s-%s" % m.groups()
        if sc not in real_scores and not _score_seen(sc, facts):
            issues.append("unsupported scoreline %s" % sc)
    for m in re.finditer(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", text):
        name = m.group(1)
        if name in roster: continue
        if name in (facts["home"], facts["away"]): continue
        if any(name.split()[0] == r.split()[0] or name.split()[1] == r.split()[-1] for r in roster): issues.append("misspelt/unknown player '%s'" % name)
    return {"ok": not issues, "issues": issues, "citations": len(cited)}


def _score_seen(sc: str, facts: Dict) -> bool:
    h = a = 0
    if sc == "0-0": return True
    for g in facts["goals"]:
        if g["team"] == "home": h += 1
        else: a += 1
        if sc == "%d-%d" % (h, a): return True
    return False


def narrate_moment(moment, facts, persona="broadcast", lang="en"):
    mode = "template"
    text = None
    if llm_available():
        try:
            text = _complete(SYSTEM.format(persona=PERSONAS[persona], language=LANGS[lang], length="2 sentences max"),
                             json.dumps({"moment": moment, "match": facts}), 160)
            mode = "azure-openai"
        except Exception as ex:  # network/quota: degrade gracefully
            text, mode = None, "template (llm error: %s)" % type(ex).__name__
    if text is None:
        text = template_moment(moment, facts, persona, lang)
    return text, mode


def narrate_recap(facts, moments, persona="broadcast", lang="en"):
    mode, text = "template", None
    if llm_available():
        try:
            text = _complete(SYSTEM.format(persona=PERSONAS[persona], language=LANGS[lang],
                                           length="one headline line, then 120-160 words recap"),
                             json.dumps({"match": facts, "key_moments": moments}), 520)
            mode = "azure-openai"
        except Exception as ex:
            text, mode = None, "template (llm error: %s)" % type(ex).__name__
    if text is None:
        text = template_recap(facts, moments, lang)
    return text, mode
