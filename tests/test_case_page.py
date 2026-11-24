"""Progress tracker logic for the case page (pure function, no Streamlit needed)."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deal_screening.case_page import progress_steps


def view(state, waiting=()):
    return SimpleNamespace(state=state, next=tuple(waiting), interrupted=bool(waiting))


class ProgressTests(unittest.TestCase):
    def test_new_revision_waiting_for_input_review(self):
        steps = dict(progress_steps(view({"chunks": [1], "facts": [1], "decision": "pending"}, ["input_review"])))
        self.assertEqual(steps["Input review"], "current")
        self.assertEqual(steps["Memo review"], "todo")  # "pending" is not a decision
        self.assertEqual(steps["Decision"], "todo")

    def test_waiting_for_memo_review(self):
        steps = dict(progress_steps(view({"chunks": [1], "facts": [1], "calculations": {"x": 1},
                                          "decision": "revise"}, ["memo_review"])))
        self.assertEqual(steps["Valuation"], "done")
        self.assertEqual(steps["Memo review"], "current")

    def test_approved_and_ledger_path(self):
        steps = dict(progress_steps(view({"chunks": [1], "ledger_enabled": True, "ledger_reviewed": True,
                                          "calculations": {"x": 1}, "decision": "approved"})))
        self.assertEqual(steps["Ledger review"], "done")
        self.assertEqual(steps["Approved"], "done")
        self.assertNotIn("AI extraction", steps)


if __name__ == "__main__":
    unittest.main()
