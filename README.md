# PitchSide — real-time, explainable match intelligence
Synthetic football events → **ingest → interpret → explain → render → personalise**, live, in 9 languages, with Azure
OpenAI / Foundry narration that is fact-checked against the data before it reaches the screen.

## Run (Python 3.12 - Microsoft Agent Framework needs >= 3.10)
    uv venv --python 3.12 .venv312 && uv pip install --python .venv312/bin/python -r requirements.txt pytest httpx playwright
    .venv312/bin/uvicorn main:app --port 8000   # http://localhost:8000  (press "Start live match")
    ./scripts/preflight.sh                      # tests + scripted demo walkthrough + every control, in a real browser

## The five-stage pipeline
| Stage | Where | What |
|---|---|---|
| Ingest | `engine/simulate.py`, `POST /api/ingest` | possession-driven synthetic match (passes w/ distance·difficulty·ball speed, shots w/ xG·shot speed, tackles, interceptions, pressure, possession changes, sprints, cards) or any external feed |
| Interpret | `engine/live.py` | incremental engine, ~0.25 ms/event: xG, momentum, win probability, per-player passing/speed/distance, rolling 10-min control metrics |
| Explain | `engine/live.py` `_insights` | control vs chaos, pressing (PPDA), momentum flips, milestones - each insight carries its numeric evidence |
| Render | `GET /api/live` (SSE), `GET /api/overlays` | language-neutral, timed, machine-readable overlays: `player_tag` (name + speed/distance indicators), `pass_card`, `shot_card`, `lower_third` |
| Personalise | `engine/personalize.py` | analyst vs fan, language, favourite club, favourite players, **follow-one-player** mode, single-metric ticker |

One feed fans out to many subscribers (`engine/hub.py`); each viewer gets their own rendering. Major moments are upgraded
with Azure OpenAI prose and verified (citations, scorelines, names) before sending.

## Multi-agent Match Desk (Microsoft Agent Framework)
`engine/crew.py`: **Analyst** (only agent with match-data tools) → **Storyteller** (persona + language, never sees raw data) →
**Verifier** (fact-check tool; `REVISE` sends the draft back, max 2 loops) → server-side re-verification. With
`FOUNDRY_PROJECT_ENDPOINT` set the agents run on a Foundry model (`FoundryChatClient`); otherwise the *same* agents run
against an in-process scripted model (`engine/offline_model.py`, not an LLM) so everything works offline. Tick
"inject a hallucination" in the UI to watch the Verifier catch an invented fact and the Storyteller repair it. `POST /api/crew`.

## API
`POST /api/feed/start?seed=3&speed=60` · `GET /api/live?lang=es&mode=fan&team=home&players=…&focus=…&metric=speed` (SSE) ·
`POST /api/ingest` · `POST /api/feed/reset` · `GET /api/overlays` · `GET /api/match` · `GET /api/players` ·
`GET /api/moment/{id}` · `GET /api/recap` · `POST /api/agent` · `POST /api/crew` · `GET /api/autoevent` · `POST /api/dataset` · `POST /api/export`

## Auto-eventing & data
`engine/autoevent.py` derives passes, distance and ball speed from a ball-tracking stream (owner hand-offs); evaluated at
~100 % recall, ~0.4 m distance error with 0.35 m tracking noise. The stream is synthesised from ground truth - plug a
vision model's output into `detect_passes()` for real video. `engine/dataset.py` generates many styled matches
(possession / direct / pressing) with a realism report vs real-world averages.

## Azure
Deploy (Container Apps + Foundry + Cosmos DB + Event Hubs, keyless via managed identity):
    az group create -n pitchside-rg -l swedencentral
    az deployment group create -g pitchside-rg -f infra/main.bicep
    azd deploy web
Env: `AZURE_OPENAI_ENDPOINT/DEPLOYMENT` (+`AZURE_OPENAI_API_KEY` or `AZURE_USE_ENTRA=1`), `FOUNDRY_PROJECT_ENDPOINT`,
`COSMOS_ENDPOINT`, `EVENTHUB_NAMESPACE/EVENTHUB_NAME`, `FABRIC_WORKSPACE/FABRIC_LAKEHOUSE`. Without them everything runs
on deterministic offline fallbacks. Fabric steps: `fabric/README.md`. Docs: `docs/`.
