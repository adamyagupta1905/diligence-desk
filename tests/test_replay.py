"""Record/replay layer and demo mode (no API calls)."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deal_screening import demo
from deal_screening.models import Memo
from deal_screening.replay import (Cassette, RecordingClient, RecordingEmbedder, ReplayClient,
                                   ReplayEmbedder, ReplayMiss)


class FakeGenerator:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt, schema, model_name):
        self.calls += 1
        return Memo(observations=[], diligence_questions=[f"Q for {prompt}"])


class FakeEmbedder:
    model_name = "fake-embed"

    def __init__(self):
        self.calls = 0

    def embed(self, texts, task):
        self.calls += 1
        return [[0.1234567, -0.9876543, float(len(t))] for t in texts]


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "cassette.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_record_then_replay_same_answer_without_calling_model(self):
        inner = FakeGenerator()
        recorder = RecordingClient(inner, Cassette(self.path))
        first = recorder.generate("prompt A", Memo, "m")
        again = recorder.generate("prompt A", Memo, "m")
        self.assertEqual(inner.calls, 1)  # second answer came from the cassette
        replayed = ReplayClient(Cassette(self.path)).generate("prompt A", Memo, "m")
        self.assertEqual(first, again)
        self.assertEqual(first, replayed)

    def test_unrecorded_request_is_a_visible_miss(self):
        RecordingClient(FakeGenerator(), Cassette(self.path)).generate("prompt A", Memo, "m")
        replay = ReplayClient(Cassette(self.path))
        for prompt, model in [("prompt B", "m"), ("prompt A", "other-model")]:
            with self.assertRaises(ReplayMiss) as caught:
                replay.generate(prompt, Memo, model)
            self.assertIn("own API key", str(caught.exception))

    def test_embeddings_replay_exactly_what_recording_returned(self):
        inner = FakeEmbedder()
        recorder = RecordingEmbedder(inner, Cassette(self.path))
        recorded = recorder.embed(["alpha", "beta"], "document")
        recorder.embed(["alpha"], "document")
        self.assertEqual(inner.calls, 1)
        replayed = ReplayEmbedder(Cassette(self.path), "fake-embed").embed(["alpha", "beta"], "document")
        self.assertEqual(recorded, replayed)  # identical, so search ranks identically
        self.assertAlmostEqual(recorded[0][0], 0.1234567, places=3)  # float16 precision
        with self.assertRaises(ReplayMiss):
            ReplayEmbedder(Cassette(self.path), "fake-embed").embed(["alpha"], "query")

    def test_workflow_shows_replay_miss_as_extraction_error(self):
        from deal_screening.ai import FactExtractor, MemoWriter
        from deal_screening.cases import CaseService
        from deal_screening.examples import SAMPLE_TEXT
        from deal_screening.storage import CaseRepository
        from deal_screening.workflow import ScreeningWorkflow
        client = ReplayClient(Cassette(self.path))
        service = CaseService(CaseRepository(Path(self.dir.name) / "data"),
                              lambda: ScreeningWorkflow(FactExtractor(client), MemoWriter(client)))
        rid = service.create("t", SAMPLE_TEXT.encode(), "x.txt", "m", "a", "r")
        state = service.view(rid).state
        self.assertIn("recorded", state["extraction_error"])
        self.assertTrue(all(f["status"] == "missing" for f in state["facts"]))

    def test_demo_documents_exist_and_load(self):
        for entry in demo.DOCUMENTS:
            source, name = demo.document_bytes(entry)
            self.assertTrue(source.strip(), name)


class DemoModeAppTests(unittest.TestCase):
    def test_demo_mode_offers_demo_documents_and_needs_no_key(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(demo, "CASSETTE", Path(directory) / "empty_cassette.json"), \
                patch.dict("os.environ", {"DEMO_MODE": "1", "GEMINI_API_KEY": "", "RECORD_CASSETTE": ""}), \
                patch.object(sys, "argv", ["app.py"]), \
                patch("deal_screening.llm.genai.Client") as network:
            demo.cassette.cache_clear()
            try:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"),
                                        default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.get("file_uploader")), 0)
                self.assertEqual(app.selectbox(key="demo_document").options,
                                 [d[0] for d in demo.DOCUMENTS])
                self.assertTrue(any("Public demo" in i.value for i in app.sidebar.info))
                app.text_input(key="new_author").input("Visitor")
                next(b for b in app.button if b.label == "Start new screening").click().run()
                self.assertFalse(app.exception)
                # Nothing recorded in this empty cassette: a clear error, not invented facts.
                self.assertTrue(any("recorded" in e.value for e in app.error))
                network.assert_not_called()
            finally:
                demo.cassette.cache_clear()


if __name__ == "__main__":
    unittest.main()
