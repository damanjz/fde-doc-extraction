"""FastAPI app: POST /extract, GET/POST /review/{id}, CSV exports. Runs on port 8300 (see README)."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse

from app import llm, pipeline, review
from app.config import settings
from app.db import Db
from app.extract import ExtractionError
from app.llm import LLMError
from app.textlayer import MAX_BYTES, UploadError

app = FastAPI(title="Larkfield document extraction (simulated customer)")
MEDIA = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg"}


@lru_cache(maxsize=1)
def _default_db() -> Db:
    return Db(settings.db_path)


def get_db() -> Db:
    return _default_db()


def get_chat() -> Callable:
    return llm.chat


def same_origin(request: Request) -> None:
    """Refuse a cross-site POST (CSRF) to the local review page: when the browser names an Origin (or a Referer),
    its host must be the host this request was sent to. Requests without either, such as curl, pass."""
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if origin and (origin == "null" or urlsplit(origin).netloc != request.headers.get("host", "")):
        raise HTTPException(403, "cross-site request refused")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ollama": "ok" if llm.ping() else "unreachable", "model": settings.ollama_model}


@app.post("/extract")
def extract_upload(
    request: Request,
    file: UploadFile,
    db: Db = Depends(get_db),
    chat: Callable = Depends(get_chat),
    _: None = Depends(same_origin),
):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES + 64 * 1024:
        raise HTTPException(413, f"file larger than {MAX_BYTES // (1024 * 1024)} MB")
    data = file.file.read(MAX_BYTES + 1)
    try:
        doc_id, res = pipeline.ingest(db, data, file.filename or "upload", chat)
    except UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except LLMError as exc:  # the model server is down or failing: not the caller's fault
        raise HTTPException(502, f"extraction failed: {exc}") from exc
    except ExtractionError as exc:  # the model answered with something unusable for this document
        raise HTTPException(422, f"extraction failed: {exc}") from exc
    return {
        "id": doc_id,
        "doc_type": res.doc_type,
        "source": res.source,
        "record": res.record,
        "fields": res.fields,
        "flags": res.flags,
        "warnings": res.warnings,
        "low_confidence": res.low_fields,
        "timings": res.timings,
        "review_url": f"/review/{doc_id}",
    }


def _load(db: Db, doc_id: int) -> tuple[dict, dict]:
    doc = db.get_document(doc_id)
    ext = db.get_extraction(doc_id) if doc else None
    if not doc or not ext:
        raise HTTPException(404, "no such document")
    return doc, ext


@app.get("/review/{doc_id}", response_class=HTMLResponse)
def review_page(doc_id: int, saved: int = 0, db: Db = Depends(get_db)):
    doc, ext = _load(db, doc_id)
    return review.page(doc, ext, db.get_reviews(doc_id), saved=bool(saved))


@app.post("/review/{doc_id}")
async def review_save(doc_id: int, request: Request, db: Db = Depends(get_db), _: None = Depends(same_origin)):
    doc, ext = _load(db, doc_id)
    form = {k: str(v) for k, v in (await request.form()).items()}
    rows, errors = review.decide(doc, ext, form, db.get_reviews(doc_id))
    if errors:
        html = review.page(
            doc,
            ext,
            db.get_reviews(doc_id),
            errors=errors,
            submitted={k[3:]: v for k, v in form.items() if k.startswith("v::")},
        )
        return HTMLResponse(html, status_code=422)
    db.save_reviews(doc_id, rows)
    done = db.get_reviews(doc_id)
    pending = [p for p, f in ext["fields"].items() if f["confidence"] == "low" and p not in done]
    db.set_status(doc_id, "extracted" if pending else "reviewed")
    return RedirectResponse(f"/review/{doc_id}?saved=1", status_code=303)


@app.get("/files/{doc_id}")
def get_file(doc_id: int, db: Db = Depends(get_db)):
    doc = db.get_document(doc_id)
    if not doc or not doc["file_path"] or not Path(doc["file_path"]).exists():
        raise HTTPException(404, "file not stored")
    return FileResponse(doc["file_path"], media_type=MEDIA[doc["kind"]], content_disposition_type="inline")


@app.get("/export.csv")
def export_csv(all: int = 0, db: Db = Depends(get_db)):
    """One row per document with final values: reviewed and auto-accepted documents, or every one with ?all=1."""
    items = []
    for d in reversed(db.list_documents()):
        if d["status"] == "error" or (not all and d["status"] not in ("reviewed", "auto_accepted")):
            continue
        items.append((db.get_document(d["id"]), db.get_extraction(d["id"]), db.get_reviews(d["id"])))
    return PlainTextResponse(
        review.export_csv(items),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=extractions.csv"},
    )


@app.get("/export/decisions.csv")
def export_decisions(db: Db = Depends(get_db)):
    return PlainTextResponse(
        review.decisions_csv(db.all_reviews()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=decisions.csv"},
    )


HOME_JS = (
    "document.getElementById('go').onclick=async()=>{"
    "const f=document.getElementById('f').files[0],m=document.getElementById('msg');"
    "if(!f){m.textContent='Choose a file first.';return}m.textContent='Extracting...';"
    "const b=new FormData();b.append('file',f);"
    "try{const r=await fetch('/extract',{method:'POST',body:b});const j=await r.json();"
    "if(r.ok){location=j.review_url}else{m.textContent=j.detail||'failed'}}catch(e){m.textContent='failed: '+e}};"
)


@app.get("/", response_class=HTMLResponse)
def home(db: Db = Depends(get_db)):
    rows = "".join(
        f'<tr><td><a href="/review/{d["id"]}">#{d["id"]}</a></td><td>{escape(d["filename"])}</td>'
        f"<td>{escape(d['doc_type'] or '')}</td><td>{escape(d['source'] or '')}</td><td>{escape(d['status'])}</td></tr>"
        for d in db.list_documents()
    )
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Document extraction</title><style>{review.STYLE}main{{display:block;max-width:900px}}</style>"
        "<header><h1>Document extraction</h1><span class=note>Larkfield Coffee Co. (simulated customer). "
        "Local only: nothing leaves this machine.</span></header><main><section class=panel>"
        "<h2>Upload a supplier invoice or courier delivery note</h2>"
        "<div class=bar><input type=file id=f accept='.pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg'>"
        "<button class=primary id=go>Extract</button><span id=msg class=note>PDF, PNG or JPEG, up to 10 MB and 10 pages. "
        "Takes up to a minute.</span></div>"
        f"<h2>Documents</h2><table><tr><th>ID</th><th>File</th><th>Type</th><th>Source</th><th>Status</th></tr>{rows}</table>"
        "<div class=bar><a href='/export.csv'>Export reviewed and auto-accepted (CSV)</a>"
        "<a href='/export/decisions.csv'>Export decisions (CSV)</a></div>"
        f"</section></main><script>{HOME_JS}</script>"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8300)
