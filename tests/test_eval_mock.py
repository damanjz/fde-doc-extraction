"""The eval runner end to end with the mocked LLM on a handful of documents."""

import json

import pytest

from app.llm import LLMResponse
from evals import mock_llm
from evals import run as ev
from evals.report import render_report
from tests.conftest import TRUTH

IDS = ["dn_003", "inv_003", "inv_004", "inv_005", "inv_013", "inv_023", "inv_002"]


@pytest.fixture()
def done(tmp_path, mock):
    truths = [TRUTH[i] for i in sorted(IDS)]
    ev.process(truths, tmp_path / "res", mock, resume=False)
    return truths, tmp_path / "res", mock


def test_results_are_saved_per_document(done):
    truths, out, _ = done
    for t in truths:
        saved = json.loads((out / f"{t['id']}.json").read_text("utf-8"))
        assert saved["id"] == t["id"] and saved["record"] is not None and not saved["failed"]


def test_scores_and_report_show_caught_and_silent_errors(done):
    truths, out, mock = done
    docs = ev.score_all(truths, out)
    by_id = {d.id: d for d in docs}
    assert all(c.correct for c in by_id["inv_003"].pipeline)  # clean doc
    # planted GSTIN, planted duplicate (inv_003 is stored first, ids are processed in order)
    assert "supplier_gstin" in by_id["inv_005"].flagged_paths
    assert "invoice_number" in by_id["inv_023"].flagged_paths
    # injected error caught (name not in source) and injected error that is silent (qty and rate swapped)
    assert "supplier_name" in by_id["inv_004"].flagged_paths
    assert by_id["inv_013"].flagged_paths == set()
    assert any(not c.correct for c in by_id["inv_013"].pipeline)
    # invalid JSON on the first reply: the repair round rescued it, for both variants
    assert by_id["inv_002"].repaired and not by_id["inv_002"].failed_baseline and not by_id["inv_002"].failed_pipeline

    report = render_report(
        docs, mock=True, model="mock", generated="2026-11-10 00:00", injected=mock.injected, partial=True
    )
    assert "MOCK RUN" in report and "mocked LLM" in report and "Silent error rate" in report
    assert "inv_013" in report and "line_items[0].qty" in report  # the silent error is listed
    assert "Expected flags: **2/2 flagged by the expected rule**" in report  # inv_005 gstin + inv_023 duplicate
    assert "Bad straight-through" in report and "Line items: two ways to align" in report


def test_every_run_starts_from_a_fresh_database(tmp_path, mock):
    truths = [TRUTH["inv_003"], TRUTH["inv_023"]]
    ev.process(truths, tmp_path / "r", mock, resume=False)
    first = json.loads((tmp_path / "r" / "inv_023.json").read_text("utf-8"))
    assert "validation:duplicate_invoice_number" in first["low"]["invoice_number"]
    # rerun only the duplicate: the old database must not leak in, so there is nothing to duplicate
    ev.process([TRUTH["inv_023"]], tmp_path / "r", mock, resume=False)
    again = json.loads((tmp_path / "r" / "inv_023.json").read_text("utf-8"))
    assert "invoice_number" not in again["low"]


def test_documents_run_in_id_order(tmp_path):
    seen = []

    class Spy(mock_llm.MockLLM):
        def __call__(self, messages):
            seen.append(self.current)
            return super().__call__(messages)

    spy = Spy(TRUTH)
    truths = ev.load_truths(["inv_003", "dn_001", "inv_002"], None)
    ev.process(truths, tmp_path / "r", spy, resume=False)
    assert [t["id"] for t in truths] == ["dn_001", "inv_002", "inv_003"]
    assert seen[0] == "dn_001" and seen[-1] == "inv_003"


def test_replay_chat_serves_saved_replies_then_goes_live():
    saved = [{"content": "{}", "latency_ms": 7, "prompt_tokens": 1, "completion_tokens": 2, "model": "m"}]

    def live(_m):
        return LLMResponse(content='{"live": 1}', latency_ms=3)

    rc = ev.ReplayChat(saved, live)
    assert rc([]).content == "{}" and rc([]).content == '{"live": 1}'
    assert len(rc.recorded) == 2


def test_resume_does_not_call_the_live_client(tmp_path, monkeypatch):
    calls = []
    truths = [TRUTH["inv_003"]]

    def live(messages):
        calls.append(1)
        return LLMResponse(content=json.dumps(TRUTH["inv_003"]["fields"]), latency_ms=11, model="x")

    monkeypatch.setattr(ev.llm, "chat", live)
    ev.process(truths, tmp_path / "r", None, resume=False)
    assert len(calls) == 1
    ev.process(truths, tmp_path / "r", None, resume=True)
    assert len(calls) == 1  # replayed from the saved reply
    saved = json.loads((tmp_path / "r" / "inv_003.json").read_text("utf-8"))
    assert saved["replies"][0]["latency_ms"] == 11


def test_a_failed_extraction_is_scored_not_crashed(tmp_path, monkeypatch):
    def junk(messages):
        return LLMResponse(content="not json", latency_ms=1)

    monkeypatch.setattr(ev.llm, "chat", junk)
    ev.process([TRUTH["inv_003"]], tmp_path / "r", None, resume=False)
    docs = ev.score_all([TRUTH["inv_003"]], tmp_path / "r")
    assert docs[0].failed_pipeline and docs[0].failed_baseline
    p = ev.scoring.variant_stats(docs, "pipeline")
    assert p["correct"] == 0 and p["silent"] == 0 and p["auto"] == 0 and p["touched"] == 1
    assert ev.scoring.variant_stats(docs, "baseline")["touched"] == 1


def test_mock_injected_errors_are_all_applied_in_a_full_plan(mock):
    """Every injected error in the plan lands on a real path of its document."""
    for doc_id in mock_llm.INJECTED:
        mock.current = doc_id
        out = mock([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
        assert mock.injected[doc_id], doc_id
        assert out.content
    assert sum(len(v) for v in mock.injected.values()) == len(mock_llm.INJECTED)
