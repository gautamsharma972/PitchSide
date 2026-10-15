# Recording checklist
1. `./scripts/preflight.sh` — restarts the server, runs the tests, and drives the whole demo in a real browser
   (14 checks, fails on any console error or HTTP error; screenshots land in `export/demo_shots/`). Must say READY TO RECORD.
2. Open `http://localhost:8000/?demo=1&theme=dark`, window 1920×1080, browser zoom 100 %, notifications off.
3. Press **h** to hide the director panel if you don't want it in frame; **→** / **←** step through the script.
4. The match is fixed (seed 146, 3–1, brace) so every take is identical; 100× replay finishes in ~1 minute.
5. Take 2: restart the live feed by pressing **Restart** in the director (or Start live match) — no page reload needed.
6. With Azure connected, narration (step 7) and agent answers (step 8) will read differently from the offline text;
   rehearse once with credentials set, and re-run preflight (it still passes — checks are content-agnostic).
Known limits to avoid on camera: Foundry/Event Hubs/Fabric badges stay "offline fallback" until deployed; non-English
text beyond the built-in phrasebook needs Azure OpenAI.
