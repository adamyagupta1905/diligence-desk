"""Record real model responses once, then replay them for a key-free public demo.

A "cassette" is a JSON file mapping a hash of each request to its response:
  generate:  sha256(model + schema name + prompt)  →  JSON text of the answer
  embed:     sha256(model + task + text)           →  vector (float16, base64)

Recording wraps the real Gemini clients: on a cache miss it calls Gemini and
stores the answer. Replay never touches the network: an unrecorded request
raises ReplayMiss, which the workflow shows as a visible error, the same way it
shows any provider failure. Nothing is invented.

Embedding vectors are stored as float16 to keep the file small. The recorder
returns the SAME rounded vectors it stores, so recorded and replayed search
rankings are identical, and therefore so are the extraction prompts.
"""
import base64
import hashlib
import json
from pathlib import Path
import threading



class ReplayMiss(ValueError):
    """No recorded response exists for this exact request."""
    user_facing = True  # safe_api_error shows this message as-is


def _key(*parts):
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _pack(vector):
    import struct
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode()


def _unpack(text):
    import struct
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


class Cassette:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {"meta": {}, "generate": {}, "embed": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        self._lock = threading.Lock()

    @property
    def meta(self):
        return self.data.setdefault("meta", {})

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))


MISS_MESSAGE = ("Demo mode replays responses recorded from a real Gemini run, and none was recorded "
                "for this exact request (for example, after changing facts or assumptions). "
                "Run DiligenceDesk locally with your own API key for live extraction and drafting.")


class ReplayClient:
    """Drop-in for GeminiClient that only answers from the cassette."""

    def __init__(self, cassette):
        self.cassette = cassette
        self.last_usage = {}

    def generate(self, prompt, schema, model_name):
        text = self.cassette.data["generate"].get(_key(model_name, schema.__name__, prompt))
        if text is None:
            raise ReplayMiss(MISS_MESSAGE)
        return schema.model_validate_json(text)


class ReplayEmbedder:
    def __init__(self, cassette, model_name):
        self.cassette, self.model_name = cassette, model_name

    def embed(self, texts, task):
        store = self.cassette.data["embed"]
        keys = [_key(self.model_name, task, t) for t in texts]
        if any(k not in store for k in keys):
            raise ReplayMiss(MISS_MESSAGE)
        return [_unpack(store[k]) for k in keys]


class RecordingClient:
    """Wraps a real client: answers from the cassette if possible, else calls and stores."""

    def __init__(self, inner, cassette):
        self.inner, self.cassette = inner, cassette
        self.last_usage = {}

    def generate(self, prompt, schema, model_name):
        key = _key(model_name, schema.__name__, prompt)
        store = self.cassette.data["generate"]
        if key not in store:
            result = self.inner.generate(prompt, schema, model_name)
            store[key] = result.model_dump_json()
            self.cassette.save()
        return schema.model_validate_json(store[key])


class RecordingEmbedder:
    def __init__(self, inner, cassette):
        self.inner, self.cassette = inner, cassette
        self.model_name = inner.model_name

    def embed(self, texts, task):
        store = self.cassette.data["embed"]
        keys = [_key(self.model_name, task, t) for t in texts]
        missing = [(k, t) for k, t in zip(keys, texts) if k not in store]
        if missing:
            vectors = self.inner.embed([t for _, t in missing], task)
            if len(vectors) != len(missing):
                raise ValueError("Embedding count does not match input count")
            for (k, _), v in zip(missing, vectors):
                store[k] = _pack(v)
            self.cassette.save()
        # Return the stored (float16-rounded) vectors so replay ranks identically.
        return [_unpack(store[k]) for k in keys]
