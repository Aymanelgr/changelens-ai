import hashlib
import json
import math
import re

import pytest

from app.core.config import get_settings
from app.llm.client import LlmResponse, LlmUnavailable
from app.modules.analysis import pipeline


@pytest.fixture(autouse=True)
def _integration_tests_start_empty(clean_tables):
    """Unit tests never touch the database. Every integration test starts from empty tables."""


class FakeEmbedder:
    """Bag of words hashed into a vector: texts sharing words are close, and it is deterministic.
    It proves the plumbing. Whether real vectors are good is the evaluation's question."""

    model_name = "fake-embedder"

    def __init__(self):
        self.embedded_texts = []

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * 128
        for word in re.findall(r"\w{3,}", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[digest[0] % 128] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts):
        self.embedded_texts += texts
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


class FakeLlm:
    """A stand-in model. `decide` maps one excerpt to a finding, or None to skip it."""

    is_local = True

    def __init__(self, decide, model="fake-llm:1"):
        self.decide = decide
        self.model = model
        self.calls = 0
        self.prompts = []

    def generate_json(self, system, user, schema):
        self.calls += 1
        self.prompts.append(user)
        findings = []
        pattern = r"\[(E\d+)\] ([^\n]*)\n<extrait>\n(.*?)\n</extrait>"
        for match in re.finditer(pattern, user, re.DOTALL):
            answer = self.decide(match.group(1), match.group(2), match.group(3))
            if answer is not None:
                findings.append(answer)
        return LlmResponse(json.dumps({"findings": findings}), 100, 20)


class DownLlm:
    is_local = True
    model = "down:1"

    def __init__(self):
        self.calls = 0

    def generate_json(self, system, user, schema):
        self.calls += 1
        raise LlmUnavailable("connection refused")


def llm_finding(evidence_id, verdict="LIKELY_IMPACTED", quote=None, explanation="Explication."):
    evidence = [{"evidence_id": evidence_id, "quote": quote, "role": "SUPPORTS"}] if quote else []
    return {
        "candidate_id": evidence_id,
        "verdict": verdict,
        "impact_type": "LOGIC_REVIEW",
        "explanation": explanation,
        "evidence": evidence,
        "uncertainties": ["Incertitude de test."],
    }


@pytest.fixture
def install_ai(monkeypatch):
    """Switches the pipeline on with fakes. Everything else in the run is the real code."""
    settings = get_settings()

    def install(llm=None, embedder=None):
        monkeypatch.setattr(settings, "llm_provider", "ollama" if llm else "none")
        monkeypatch.setattr(settings, "embedding_provider", "sentence-transformers")
        monkeypatch.setattr(pipeline, "get_llm_client", lambda model=None: llm)
        chosen = embedder or FakeEmbedder()
        monkeypatch.setattr(pipeline, "get_embedder", lambda: chosen)
        monkeypatch.setattr(settings, "search_min_cosine", 0.1)
        monkeypatch.setattr(settings, "search_strong_cosine", 0.15)
        return chosen

    return install
