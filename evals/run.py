"""Eval runner.

Usage:
  python -m evals.run                       # live eval against Ollama (fusion27b:iq3), writes evals/report.md
  python -m evals.run --mock                # mocked LLM (ground truth + injected errors), writes evals/report-mock.md
  python -m evals.run --resume              # reuse saved LLM replies for documents already done
  python -m evals.run --report-only         # rebuild the report from saved results
  python -m evals.run --ids inv_001,scan_002 --limit 5

Every run starts from a fresh SQLite file and processes the documents in id order (dn_, inv_, scan_), because the
duplicate-invoice check depends on what is already stored.

Both variants come from the same run and the same extraction (repair round included). The baseline is that
extraction without validation, confidence or warnings; the pipeline adds them. Validation never changes a value, so
field accuracy is identical by construction and the variants differ only in what is flagged.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from app import llm, pipeline  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Db  # noqa: E402
from app.llm import LLMResponse  # noqa: E402
from evals import scoring  # noqa: E402
from evals.report import render_report  # noqa: E402

TRUTH_DIR = ROOT.parent / "data" / "truth"
DOCS_DIR = ROOT.parent / "data" / "docs"


class ReplayChat:
    """Serve saved replies for a document (--resume), then fall back to the live client. Records everything."""

    def __init__(self, saved: list[dict], live) -> None:
        self.saved, self.live, self.recorded = saved, live, []

    def __call__(self, messages: list[dict]) -> LLMResponse:
        i = len(self.recorded)
        if i < len(self.saved):
            r = LLMResponse(**self.saved[i])
        else:
            r = self.live(messages)
        self.recorded.append(r.__dict__.copy())
        return r


def load_truths(ids: list[str] | None, limit: int | None) -> list[dict]:
    truths = [json.loads(p.read_text("utf-8")) for p in sorted(TRUTH_DIR.glob("*.json"))]
    if not truths:
        sys.exit("no ground truth found: run `python -m data.generate` first")
    if ids:
        truths = [t for t in truths if t["id"] in set(ids)]
    return truths[:limit] if limit else truths


def process(truths: list[dict], out_dir: Path, mock, resume: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "eval.db"
    for ext in ("", "-journal", "-wal", "-shm"):
        Path(str(db_path) + ext).unlink(missing_ok=True)  # fresh database every run
    db = Db(db_path)
    t_start = time.monotonic()
    for n, truth in enumerate(truths, 1):
        doc_id = truth["id"]
        saved_path = out_dir / f"{doc_id}.json"
        saved = []
        if resume and saved_path.exists():
            saved = json.loads(saved_path.read_text("utf-8")).get("replies", [])
        if mock is not None:
            mock.current = doc_id
            chat = mock
            replay = None
        else:
            replay = chat = ReplayChat(saved, llm.chat)
        data = (DOCS_DIR / truth["file"]).read_bytes()
        t0 = time.monotonic()
        out: dict = {"id": doc_id, "failed": False, "error": None}
        try:
            _, res = pipeline.ingest(db, data, truth["file"], chat, keep_file=False)
            out.update(
                record=res.record,
                low={p: f["reasons"] for p, f in res.fields.items() if f["confidence"] == "low"},
                warnings=res.warnings,
                doc_type=res.doc_type,
                timings=res.timings,
                repaired=res.repaired,
                raw_reply=res.raw_reply,
            )
        except Exception as exc:  # LLMError, ExtractionError, UploadError, or anything else: record it, keep going
            out.update(
                record=None,
                low={},
                warnings=[],
                doc_type=None,
                timings={},
                failed=True,
                error=f"{type(exc).__name__}: {exc}",
            )
        if replay is not None:
            out["replies"] = replay.recorded
        saved_path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", "utf-8")
        status = "FAILED " + str(out["error"])[:60] if out["failed"] else f"{len(out['low'])} low"
        print(
            f"[{n}/{len(truths)}] {doc_id} {time.monotonic() - t0:5.1f}s  {status}  "
            f"(elapsed {time.monotonic() - t_start:.0f}s)",
            flush=True,
        )


def score_all(truths: list[dict], out_dir: Path) -> list[scoring.DocScore]:
    docs = []
    for truth in truths:
        res = json.loads((out_dir / f"{truth['id']}.json").read_text("utf-8"))
        docs.append(scoring.score_doc(truth, res))
    return docs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="use the mocked LLM (not a model result)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    truths = load_truths([i for i in args.ids.split(",") if i], args.limit or None)
    out_dir = ROOT / "results" / ("mock" if args.mock else "live")
    mock = None
    if args.mock:
        from evals.mock_llm import MockLLM

        mock = MockLLM({t["id"]: t for t in load_truths(None, None)})
    elif not args.report_only and not llm.ping():
        sys.exit(f"Ollama is not reachable at {settings.ollama_url}. Start it, or use --mock.")
    if not args.report_only:
        process(truths, out_dir, mock, args.resume)
    docs = score_all(truths, out_dir)
    report = render_report(
        docs,
        mock=args.mock,
        model="mock" if args.mock else settings.ollama_model,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        injected=mock.injected if mock else None,
        partial=bool(args.ids or args.limit),
    )
    path = ROOT / ("report-mock.md" if args.mock else "report.md")
    if args.ids or args.limit:
        path = ROOT / ("report-mock-partial.md" if args.mock else "report-partial.md")
    path.write_text(report, "utf-8")
    print(report)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
