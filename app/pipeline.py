"""The whole pipeline: text layer -> LLM extraction -> validation -> confidence -> storage."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import confidence, extract, normalise, triage, validate
from app.config import settings
from app.db import Db
from app.llm import LLMError
from app.textlayer import TextDoc, UploadError, read_document


@dataclass
class Result:
    doc_type: str
    source: str  # pdf | scan
    text: str
    record: dict  # canonical record, same shape as the ground-truth `fields`
    fields: dict[str, dict]  # path -> {value, confidence, reasons}
    flags: list[dict]
    warnings: list[str]
    timings: dict
    raw_reply: str
    model: str = ""
    repaired: bool = False
    typed: TextDoc | None = field(default=None, repr=False)

    @property
    def low_fields(self) -> list[str]:
        return [p for p, f in self.fields.items() if f["confidence"] == "low"]

    @property
    def status(self) -> str:
        """auto_accepted: nothing for a person to look at. Any low field or any document warning (including an
        uncertain document type) means a human touch."""
        return "extracted" if self.low_fields or self.warnings else "auto_accepted"


EXT = {"pdf": "pdf", "png": "png", "jpeg": "jpg"}


def run(td: TextDoc, prior: Callable[[str], list[dict]], chat=None) -> Result:
    """Everything after the text layer. `prior(doc_type)` returns records already stored (duplicate check)."""
    t0 = time.monotonic()
    doc_type, confident = triage.classify(td.text)
    warnings: list[str] = []
    if not confident:
        warnings.append("document_type_uncertain")
    if triage.injection_warning(td.text):
        warnings.append("instruction_in_document")
    text = td.text
    if len(text) > settings.max_prompt_chars:
        text = text[: settings.max_prompt_chars]
        warnings.append("text_truncated")
    ex = extract.extract(doc_type, text, chat)
    t1 = time.monotonic()
    try:
        record, bad = normalise.canonical(doc_type, ex.data)
        flags = validate.validate(doc_type, record, bad, prior(doc_type), td.text)
        fields = confidence.assess(doc_type, record, flags, td)
    except Exception as exc:  # whatever the model returned, never a crash: the document is recorded as failed
        raise extract.ExtractionError(f"unusable model output ({type(exc).__name__}: {exc})") from exc
    t2 = time.monotonic()
    timings = {
        "text_ms": td.ocr_ms,
        "llm_ms": ex.llm_ms,
        "llm_first_ms": ex.first_ms,
        "check_ms": int((t2 - t1) * 1000),
        "total_ms": td.ocr_ms + int((t2 - t0) * 1000),
        "prompt_tokens": ex.prompt_tokens,
        "completion_tokens": ex.completion_tokens,
    }
    return Result(
        doc_type,
        "scan" if td.is_scan else "pdf",
        td.text,
        record,
        fields,
        [{"field": f.field, "rule": f.rule, "message": f.message} for f in flags],
        warnings,
        timings,
        ex.reply,
        ex.model,
        ex.repaired,
        td,
    )


def ingest(db: Db, data: bytes, filename: str, chat=None, keep_file: bool = True) -> tuple[int, Result]:
    """Process an upload and store it. Raises UploadError for rejected input, LLMError/ExtractionError
    after recording the failed document."""
    td = read_document(data)  # raises UploadError before anything is stored
    sha = hashlib.sha256(data).hexdigest()
    path = None
    if keep_file:
        up = Path(settings.upload_dir)
        up.mkdir(parents=True, exist_ok=True)
        path = str(up / f"{sha[:16]}.{EXT[td.kind]}")
        Path(path).write_bytes(data)
    safe_name = Path(filename or "upload").name[:120]
    try:
        res = run(td, db.prior_records, chat)
    except (LLMError, extract.ExtractionError) as exc:
        db.add_document(
            filename=safe_name,
            sha256=sha,
            kind=td.kind,
            doc_type=None,
            source=None,
            pages=len(td.pages),
            status="error",
            error=str(exc),
            text=td.text,
            file_path=path,
        )
        raise
    doc_id = db.add_document(
        filename=safe_name,
        sha256=sha,
        kind=td.kind,
        doc_type=res.doc_type,
        source=res.source,
        pages=len(td.pages),
        status=res.status,
        text=td.text,
        file_path=path,
    )
    db.add_extraction(
        doc_id,
        raw_reply=res.raw_reply,
        record=res.record,
        fields=res.fields,
        flags=res.flags,
        warnings=res.warnings,
        timings=res.timings,
        model=res.model,
    )
    return doc_id, res


__all__ = ["Result", "UploadError", "ingest", "run"]
