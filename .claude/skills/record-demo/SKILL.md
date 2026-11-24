---
name: record-demo
description: Re-record the key-free demo cassette (demo/cassette.json) with real Gemini calls after prompts, models or sample documents change. Paid; only run with the user's explicit OK in this conversation. Use when the user says "re-record the demo", "refresh the cassette", or the demo replays stale responses.
disable-model-invocation: true
---

# Re-record the demo cassette

This makes **paid** Gemini API calls. Do not start without a clear yes from the user in this
conversation for this run.

1. Say why a re-record is needed (which prompt/model/document changed) and confirm with the user.
2. Check the key is configured **without reading it**: the user confirms `.env` holds
   `GEMINI_API_KEY`. Never open, print or echo `.env`.
3. Run `.venv/bin/python demo/record_demo.py` (the project's `ask` permission rule prompts the user).
4. Run `.venv/bin/python app.py --check`; replay tests must still pass.
5. Launch the `diligence-public-demo` preview (port 8600, `GEMINI_API_KEY` empty) and click
   through one case to confirm it works with no key. Screenshot it.
6. Update the model name and date in PROGRESS.md, README and the commit message
   (e.g. "Record demo responses (gemini-x, YYYY-MM-DD)"). Commit only when the user asks.
