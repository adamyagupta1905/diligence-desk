"""Record the public demo's model responses, then verify they replay.

The script drives the real Streamlit UI (via Streamlit's AppTest) through each
demo document with the default facts and assumptions, once for the EV/EBITDA
method and once for DCF. Because it clicks the same widgets a visitor would,
the recorded requests are exactly the ones the public demo will replay.

  record  DEMO_MODE=1 + RECORD_CASSETTE: real Gemini calls, answers saved to
          demo/cassette.json (about 15 generation calls plus embeddings, a few cents)
  verify  DEMO_MODE=1 only: replays with no network and checks that every flow
          reaches a memo, or a deliberate validation block

From the repository root (needs GEMINI_API_KEY in .env for recording):
    .venv/bin/python demo/record_demo.py --record
    .venv/bin/python demo/record_demo.py --verify
"""
import argparse
from datetime import date
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                       # noqa: E402
from streamlit.testing.v1 import AppTest             # noqa: E402
from deal_screening import demo                      # noqa: E402
from deal_screening.replay import Cassette           # noqa: E402
from deal_screening.storage import CaseRepository    # noqa: E402

METHODS = ("ev_ebitda", "dcf")


def run_flow(label, method, data_dir, env):
    """Start a case for one demo document and confirm the default inputs."""
    with patch.dict(os.environ, {**env, "DEAL_SCREENING_DATA_DIR": data_dir}, clear=False):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=600).run()
        app.selectbox(key="demo_document").select(label).run()
        app.text_input(key="new_author").input("Demo recorder")
        next(b for b in app.button if b.label == "Start new screening").click().run()
        if app.exception:
            return "exception: " + str(app.exception[0].value)
        repo = CaseRepository(data_dir)
        rid = repo.list_cases()[0]["active_revision_id"]  # one case per temporary store
        if method == "dcf":
            app.selectbox(key="method_" + rid).select("dcf").run()
        app.text_input(key="input_reviewer_" + rid).input("Demo recorder")
        app.text_area(key="input_note_" + rid).input("Default facts and assumptions.")
        app.checkbox(key="input_check_" + rid).check()
        next(b for b in app.button if b.label == "Confirm inputs and calculate").click().run()
        if app.exception:
            return "exception: " + str(app.exception[0].value)
        errors = [e.value for e in app.error]
        if any(b.label == "Submit review" for b in app.button):
            return "memo error: " + errors[0][:120] if errors else "memo ready"
        return "blocked: " + (errors[0][:120] if errors else "no message")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="Make real Gemini calls and save them")
    group.add_argument("--verify", action="store_true", help="Replay offline and check every flow")
    args = parser.parse_args()
    env = {"DEMO_MODE": "1"}
    if args.record:
        load_dotenv(ROOT / ".env")
        if not os.getenv("GEMINI_API_KEY", "").strip():
            parser.error("Set GEMINI_API_KEY in .env to record.")
        cassette = Cassette(demo.CASSETTE)
        cassette.meta.update({"model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                              "embedding_model": os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
                              "recorded_on": date.today().isoformat()})
        cassette.save()
        demo.cassette.cache_clear()
        env["RECORD_CASSETTE"] = str(demo.CASSETTE)
    else:
        env["GEMINI_API_KEY"] = ""  # prove replay needs no key
        env["RECORD_CASSETTE"] = ""
    results = []
    for label, *_ in demo.DOCUMENTS:
        for method in METHODS:
            with tempfile.TemporaryDirectory() as data_dir:
                outcome = run_flow(label, method, data_dir, env)
            print(f"{label:<55} {method:<10} {outcome}", flush=True)
            results.append(outcome)
    if args.verify:
        bad = [r for r in results if not (r == "memo ready" or r.startswith("blocked"))]
        print(f"\n{len(results) - len(bad)}/{len(results)} flows replay cleanly.")
        raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
