# Submission pack — Inside the Game: Developer Hackathon

## Deadlines (Pacific Time) — verify on the official page
- Register: by **Oct 20, 2026, 12:00 pm**  ·  Submit: by **Oct 27, 2026, 11:59 pm**  ·  Judging until Nov 10  ·  Winners by Nov 20
- Keep the demo URL **free and unrestricted until judging ends (Nov 10)**.

## Project name: PitchSide — real-time, explainable match intelligence

## Elevator pitch (paste into the submission form)
Football stories don't end at the scoreline. PitchSide ingests synthetic football events as they happen and, in under a
millisecond per event, interprets them (xG, pass difficulty, ball/shot/player speed, distance, momentum, win
probability), explains *why* moments matter (control vs chaos, pressing, momentum swings — each with its numeric
evidence) and renders it as timed, machine-readable overlays for studio, broadcast or streaming. The same live match is
personalised per viewer: analyst or casual fan, nine languages, favourite club and players, follow-one-player mode and a
single-metric ticker. Narration is produced by a three-agent system on **Microsoft Agent Framework** — an Analyst that
alone can read match data, a Storyteller, and a Verifier that fact-checks every claim and sends bad drafts back for
repair — so broadcasters get speed *and* trust.
**Microsoft/Azure technologies:** Microsoft Foundry (agents + model deployment), Microsoft Agent Framework (multi-agent
orchestration), Azure OpenAI, Azure Container Apps (hosting), Azure Cosmos DB (narration cache/audit), Azure Event Hubs +
Microsoft Fabric (Eventstream → Eventhouse, OneLake lakehouse export), Bicep/azd with managed identity (keyless).
*(Only list what is actually deployed and working when you submit — see "Honesty check" below.)*

## Mapping to the five judging criteria (20 % each)
1. **Technological implementation** — incremental engine (~0.25 ms/event), SSE hub fanning one feed to many personalised viewers,
   tracking→events auto-eventing, Bicep IaC, 19 tests + scripted browser walkthrough.
2. **Agentic design & innovation** — Analyst/Storyteller/Verifier with tool-gated data access, verify→revise loop, deterministic
   server-side re-verification; "inject a hallucination" demo; offline-capable agent stack.
3. **Real-world impact** — live graphics + multilingual commentary for broadcasters/streamers, trust layer against LLM
   hallucination, overlay-track JSON for a rendering partner, fan personalisation by market.
4. **UX & presentation** — broadcast overlay stage, analyst/fan toggle, 9 languages incl. RTL, demo director, 90-second video.
5. **Category fit** — all five pipeline stages plus player ID, pass quality, speed metrics, auto-eventing, narratives,
   explainability, synthetic data generator, multi-language. Target prizes: Grand Prize, **Best Multi-Agent System**,
   Best Use of Microsoft Foundry, Best Azure Cloud Native Integration.

## Submission checklist
- [ ] Registered (by Oct 20) and appointed a team Representative (teams ≤ 4)
- [ ] Public GitHub repo pushed (`git init`, add, commit, push) — repo must be public; README + LICENSE included
- [ ] Deployed to Azure; public URL works with no login; `./scripts/preflight.sh`-style check run against that URL
- [ ] Demo video < 2:00, public on YouTube/Vimeo, linked on the project page (`docs/video-script-90s.md`)
- [ ] Pitch pasted (above), technologies listed accurately, English throughout
- [ ] Testing instructions: open URL → "Start live match" → use the viewer controls / "Multi-agent Match Desk"
- [ ] Azure budget/alerts set; keep the app running through Nov 10

## Honesty check (don't overclaim — Stage 1 and the rules penalise "not working as described")
- Foundry agents, Cosmos DB, Event Hubs, Fabric and the deployed Container App are **written but unverified** until you deploy.
- The multi-agent code is verified locally against a scripted stand-in model, **not** a real Foundry model.
- Data is synthetic and fictional (no real clubs, players or footage). Auto-eventing runs on a synthesised tracking stream, not video.
- Built with Claude Code; do not claim GitHub Copilot / Azure MCP usage unless you actually used them.
