"""Public demo configuration: which documents visitors can screen, and where
recorded model responses live. Enabled with the environment variable DEMO_MODE=1.

In demo mode the app never uses an API key: model calls are replayed from
demo/cassette.json (see replay.py), uploads are disabled, and each browser
session gets its own temporary case store.
"""
from functools import lru_cache
import os
from pathlib import Path

from .examples import SAMPLE_TEXT

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "demo" / "cassette.json"

# (label shown to visitors, filename, path or None for the built-in example, what it demonstrates)
DOCUMENTS = [
    ("Northstar Industrial Software (2-page example)", "fictional_northstar.txt", None,
     "Short, clean report: the basic flow end to end."),
    ("Gale Point Wind (wind farm operator)", "eval_09_gale_point_wind.txt",
     "evaluation/documents/eval_09_gale_point_wind.txt",
     "Infrastructure annual review with decoy figures across 13 pages."),
    ("Northway Tollroads (toll road concessions)", "eval_02_northway_tollroads.txt",
     "evaluation/documents/eval_02_northway_tollroads.txt",
     "Infrastructure annual review; customer concentration is not disclosed."),
    ("Skyharbour Airports (adjusted EBITDA only)", "eval_08_skyharbour_airports.txt",
     "evaluation/documents/eval_08_skyharbour_airports.txt",
     "Reports only adjusted EBITDA, in thousands: the EV/EBITDA multiple must be blocked."),
    ("Harbourline Logistics (synonym-heavy)", "16_long_annual_review_synonyms.txt",
     "sample_documents/16_long_annual_review_synonyms.txt",
     "Uses 'turnover', 'borrowings less cash' and similar wording; see the Evidence tab."),
]


@lru_cache(maxsize=1)
def cassette():
    """Loaded once per server process (app.py itself re-runs on every click)."""
    from .replay import Cassette
    return Cassette(CASSETTE)


def enabled():
    return os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}


def document_bytes(entry):
    _, filename, path, _ = entry
    return (SAMPLE_TEXT.encode() if path is None else (ROOT / path).read_bytes()), filename
