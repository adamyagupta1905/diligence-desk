# Public demo deployment

The public demo runs with `DEMO_MODE=1`:

- **No API key is used.** Gemini responses for the demo documents were recorded
  once from a real run (`demo/cassette.json`) and are replayed. Retrieval
  ranking, validation, valuation, the LangGraph workflow, reviews and approvals
  all run live.
- **Only prepared documents.** Uploads, document replacement and ledger
  re-extraction are disabled, because they would need live model calls.
- **Private sessions.** Each browser session gets its own temporary case store,
  so visitors never see each other's cases.
- **Honest misses.** If a visitor changes facts or assumptions, Python
  recalculates live, but an unrecorded memo request shows a clear "not recorded"
  message instead of an invented memo.

## Deploy on Streamlit Community Cloud (free)

1. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub.
2. **Create app** → *Deploy a public app from GitHub* →
   repository `adamyagupta1905/diligence-desk`, branch `main`, main file `app.py`.
3. **Advanced settings:** choose Python 3.13 and add this secret:
   ```toml
   DEMO_MODE = "1"
   ```
   Do **not** add a `GEMINI_API_KEY`; the demo does not need one.
4. Deploy, then put the app URL at the top of the README.

## Re-record the demo responses

Needed after changing prompts, chunking, retrieval or default assumptions
(anything that changes the exact requests). From the repository root, with
`GEMINI_API_KEY` in `.env`:

```bash
rm demo/cassette.json
.venv/bin/python demo/record_demo.py --record
.venv/bin/python demo/record_demo.py --verify
```

`--record` drives the real UI through every demo document with the default inputs
(EV/EBITDA and DCF), making about 15 generation calls plus embeddings.
`--verify` replays everything with no API key and fails if any flow misses.
