# Runbook: Larkfield document extraction

For the person operating it after hand-off. Everything runs locally. Port **8300**.

## Setup (once)
- Python 3.13 venv: `.venv` (`requirements.txt`). Pip cache and the rapidocr models (inside the wheel, about 14 MB) stay under a local cache folder.
- Ollama running with `fusion27b:iq3` (`ollama list`). The app sends `num_ctx 8192`; another project using the same model with a different context makes Ollama reload it.
- Optional `.env`-style variables (see `.env.example`): `OLLAMA_URL`, `OLLAMA_MODEL`, `DOCX_DB` (default `app.db`), `DOCX_UPLOADS` (default `uploads/`), `OCR_LOW_CONF` (default 0.70).

## Start and stop
```
python -m uvicorn app.main:app --port 8300
```
- Check: `curl http://127.0.0.1:8300/health` gives `status ok`; `ollama` should be `ok`.
- Stop: Ctrl+C in that terminal. Data stays in `app.db` and `uploads/`.
- Ports 8000, 8100, 8200, 8400 and 9000 belong to other projects.

## Daily use
1. Open http://127.0.0.1:8300, choose a PDF, PNG or JPEG (up to 10 MB, 10 pages), press Extract (up to a minute).
2. The review page opens: low-confidence fields are yellow with the reason. Confirm or correct each one, then Save decisions. Edited values are stored as corrections.
3. A document with nothing flagged and no warning is `auto_accepted` (no review needed). Otherwise it stays `extracted` until every low-confidence field has a decision, then it is `reviewed`.
4. Export: `/export.csv` (reviewed and auto-accepted documents, final values, one row each; `?all=1` includes pending ones) and `/export/decisions.csv` (every decision with the original value and the reason).

## Run the live eval
GPU-bound and shared: run one at a time. About 20 to 40 minutes.
```
python -m evals.run
```
- **Before every run, archive the previous one**: copy `evals/report.md` to `evals/report-<label>-<date>.md` and rename `evals/results/live` to `evals/results/live-<label>` (the runner overwrites both). Run 1 and run 2 (2026-11-10) are kept as `report-live1-2026-11-10.md`, `report-live2-2026-11-10.md` and `results/live-run1`, `results/live`.
- Interrupted: add `--resume` (saved LLM replies are reused; the database is still rebuilt from scratch in id order).
- Output: `evals/report.md`, per-document JSON in `evals/results/live/`. Copy the numbers into the README table, nothing else.
- Without the GPU: `python -m evals.run --mock` writes `evals/report-mock.md` (mocked LLM, proves scoring only).
- Re-run after any change to the prompt, schema, validation rules, thresholds, data or model.

## Regenerate the dataset
`python -m data.generate`, then `python data\validate.py` (must print OK) and `python -m pytest -q`. Needs the Windows fonts Arial, Calibri, Segoe UI, Times New Roman, Courier New. Any change to a case or rule is made before a run and noted in the case; never tune a case to make a run pass.

## Measure OCR on the scans
`python scripts\ocr_accuracy.py` prints character accuracy and number recall per scan.

## When something goes wrong
| Symptom | Check | Fix |
|---|---|---|
| `/extract` returns 502 | `/health` (ollama); the document appears in the list with status `error` | Start Ollama; upload again |
| Ollama HTTP 500 mentioning the system message | Code sent a second system message | A unit test fails if any request has more than one; fix the caller |
| 415 on a PDF or image | The file is not really a PDF, PNG or JPEG (checked by magic bytes, not the extension) | Re-export the file |
| 403 on a POST | The browser sent a foreign `Origin` or `Referer` (another site, or a different host name than the one you opened) | Use the same address you opened the page with |
| 422 `pages` or `image ... too large` | More than 10 pages, an image over 40 megapixels (or 80 in total, or more than 8 on a page) | Split or downscale the file |
| 422 `unusable model output` | The model answered with something the checks cannot process; the document is listed with status `error` | Upload again; if it repeats, look at `raw_reply` in `app.db` |
| 422 unreadable | Corrupt or encrypted file | Re-scan or unlock |
| Every field on a scan is flagged | Poor scan (low resolution, heavy skew) | Re-scan at 200 dpi or more; the review page still shows the image |
| A field is wrong but not flagged (silent error) | Open the extracted text tab: is the value in the document? | Correct it in the review page; if a pattern repeats, add a validation rule in `app/validate.py` with a test |
| Duplicate not detected | Supplier GSTIN and name both unreadable on one copy | Fix the supplier fields in the review page; duplicates compare extracted records |
| Eval very slow | GPU shared with another job | Wait; do not run two evals at once |
| `database is locked` | Two writers on `app.db` | Run one server; the eval uses its own file in `evals/results` |

## Known limits
Devanagari is not read (OCR models are Chinese and English). One GST rate per invoice. INR only. No GST-portal lookup: a valid GSTIN of a third company passes. Quantity and rate swapped on a line is not detected. Lines in the wrong order are not detected. The injection scan is a heuristic; validation is the protection. No authentication and no CSRF token on the local page (a foreign Origin is refused). Flags cover every field in a failed sum, because the code cannot tell which number is wrong.
