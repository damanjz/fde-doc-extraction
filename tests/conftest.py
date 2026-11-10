from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import pipeline
from app.config import settings
from app.db import Db
from evals.mock_llm import MockLLM

ROOT = Path(__file__).resolve().parents[1]
TRUTH = {p.stem: json.loads(p.read_text("utf-8")) for p in sorted((ROOT / "data" / "truth").glob("*.json"))}


def doc_bytes(doc_id: str) -> bytes:
    return (ROOT / "data" / "docs" / TRUTH[doc_id]["file"]).read_bytes()


@pytest.fixture()
def truths() -> dict[str, dict]:
    return TRUTH


@pytest.fixture()
def db(tmp_path) -> Db:
    return Db(tmp_path / "t.db")


@pytest.fixture()
def mock() -> MockLLM:
    return MockLLM(TRUTH)


@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))


def run_doc(doc_id: str, db: Db, mock: MockLLM):
    """Ingest a generated document with the mocked LLM."""
    mock.current = doc_id
    return pipeline.ingest(db, doc_bytes(doc_id), TRUTH[doc_id]["file"], mock)
