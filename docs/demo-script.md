# PitchSide — 3-minute demo script
**0:00 Hook** "Football stories don't end at the scoreline. PitchSide turns raw events into explainable, personalised,
fact-checked storytelling — live, in any language."
**0:15 Real-time (40s)** Pick 60×, **Start live match**. Show the overlay stage: player name tags, speed/distance
indicators on sprints, pass cards (distance · difficulty · ball speed), shot cards (xG · shot speed). Point at the
`ms/event` badge — the pipeline runs in a fraction of a millisecond.
**0:55 Explain (30s)** Control-vs-chaos panel + feed: "Northbridge are in control: 62 % possession, 87 % pass accuracy
over 10 min" — every insight has its numbers underneath. Click a key moment: factors incl. game phase.
**1:25 Personalise (50s)** Same live match, three viewers: flip **Analyst → Fan** (numbers drop, plain language);
set **Language = Japanese/Arabic**; pick a **favourite club** (⭐ prefix, their goals promoted); **Follow one player**
(only their tags + live card); metric ticker = top speed.
**2:15 Trust (25s)** Narration + green ✓ grounded; Foundry agent: "who was the fastest player?" with tool calls.
**2:40 Platform (20s)** Overlay-track JSON for a graphics renderer, **Auto-event detector check**, synthetic dataset
generator, Export to Fabric / Event Hubs → Eventhouse. Close: keyless, Bicep-deployable.
