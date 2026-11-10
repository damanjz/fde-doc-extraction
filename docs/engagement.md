# Engagement: Larkfield document extraction

Fictional customer (Larkfield Coffee Co., simulated), synthetic documents. This is how the engagement would be written up for a real one.

## Discovery

**Who asked.** The operations lead at Larkfield, on behalf of the two people who type supplier invoices and courier delivery notes into the purchase spreadsheet. Finance reads that spreadsheet at month end and claims input tax credit from it.

**The problem in their words.** Typing takes hours each week, and GST numbers and totals are often wrong. Wrong GSTINs block the tax credit; wrong totals surface weeks later.

**Success metric agreed.** Not "accuracy". The metric is the **silent error rate**: wrong fields that reach the spreadsheet without being flagged, as a share of all fields. Reasoning: a model that is 97% right is fine if the 3% is sent to a person, and dangerous if it is not. It is always read with the document outcomes, because "straight-through rate" alone rewards a system that waves wrong documents through: auto-accepted documents, split into correct straight-through (every field right, no planted problem) and bad straight-through (a wrong field or a planted problem got through), plus documents touched by a human and planted problems passed silently. Secondary metrics: flag recall and precision, and p50 and p95 seconds per document. Hard rule: every planted problem (bad GSTIN checksum, totals that do not add up, missing date, duplicate invoice number) must be flagged. Target for the pilot: silent error rate at or below 1% of fields, no planted problem passed silently, bad straight-through as close to zero as the checks allow. These are targets, not results; the live numbers are in `evals/report.md`.

**Assumptions made.**
- Documents are Larkfield's own inbox: supplier invoices (5 recurring layouts) and courier delivery notes.
- Rupees only, one GST rate per invoice, Latin script. Hindi-English labels appear in romanised form.
- Everything stays on the machine (local model, local OCR, SQLite): invoices carry supplier bank details.
- A person reviews flagged fields; nothing is written to the spreadsheet without a decision on every low-confidence field.
- Larkfield's buyer GSTIN is on every invoice, so the model must pick the supplier's, not the buyer's.
- The first version exports CSV; the spreadsheet import stays manual.

**Left out (on purpose).**
- Devanagari text. Reportlab cannot shape it and the bundled OCR models do not read it. This is a documented limit, not a silent failure.
- Handwriting, stamps and photos taken at an angle beyond a few degrees.
- Several GST rates on one invoice, foreign currency, credit notes.
- Lookup of the GSTIN against the GST portal (a valid number of the wrong company passes) and e-invoice IRN or QR checks.
- Purchase-order matching and ERP or accounts integration.
- Authentication on the web page: it is meant for one machine.

## What was built
A pipeline that reads a PDF or image (text layer first, OCR when a page has no text), asks the local model for JSON, checks the result in code, scores each field's confidence, and shows only the doubtful fields to a person on a local review page. Decisions are stored and exported as CSV. See the README for the flow and `docs/runbook.md` for operation.

Design choices worth defending:
- **Validation is code.** GSTIN checksum (mod 36), sums and dates are deterministic and tested rule by rule (394 tests). The model never decides whether a document is consistent, and validation never edits a value.
- **Flag every field in a failed sum.** The code cannot tell whether the total, the subtotal or the GST is wrong, so all three are flagged. Precision pays for it; the reviewer sees the relevant numbers together.
- **Verbatim check.** A value that is not in the source text is distrusted, with numbers normalised so lakh and western grouping match. This catches invented or "corrected" values.
- **Prompt injection.** The prompt tells the model the text is untrusted data; a regex scan raises a document warning on instruction-like text; and a model that obeys ("the total is 0") produces a total that fails the checks. A test covers each.

## How it was checked
- Synthetic data with ground truth as printed; planted problems listed as `expected_flags` with field and reason, scored separately from precision and recall.
- Unit tests with a mocked LLM for every validation rule, the pipeline, the API (including every upload rejection) and the scoring.
- The eval runner was proven end to end with a mocked model that returns the truth plus 12 injected errors (2 silent on purpose: quantity and rate swapped, two lines in the wrong order): all 12 expected flags raised by the expected rule, the 2 silent errors counted as bad straight-through (the baseline passes 25 documents with a wrong field or a planted problem, the pipeline 2). That proves the scoring, not the model.
- OCR measured before it was relied on: 96.0% mean character accuracy and 91.7% of numbers exact on the 10 scans. These scans are clean synthetic renders, so the numbers are optimistic; real scans will do worse.

## Results (live run 2, final; `evals/report.md`)
50 synthetic documents, `fusion27b:iq3`, fresh database, fixed order.
- **Silent errors: 0 of 1130 fields** (target: at or below 1%). All 9 wrong fields (field accuracy 99.2%) were flagged; 1 is on a text PDF, 8 on scans. The baseline, the same extraction with no checks, let all 9 wrong fields through (in 5 documents) and passed all 13 planted-problem documents: **17 bad straight-throughs against 0** for the pipeline.
- **Planted problems: 12 of 12 flagged by the expected rule**; the injected instruction in inv_019 raised the document warning and changed no value.
- **Cost of the checks:** 32 documents went straight through correctly (baseline: 33, because the baseline also passes the planted-problem documents it should not), 18 went to a person, of which 17 really had a problem. Flag precision per cell is 9/33 (27%): the cells of a flagged line list all count as flagged. Latency per document: p50 9.1 s, p95 12.1 s.
- **Run 1 to run 2:** precision 9/53 to 9/33 and correct straight-through 29 to 32 came from general OCR normalisation fixes (thousands separators misread as dots, GSTINs glued to labels) and one scoring rule, made on the same 50 documents after run 1. It is not an independent test; real scans and a different OCR are the test that matters.
- **What this does not show:** real scans (these are clean synthetic renders), mixed GST rates, Devanagari, or any document outside the five layouts.

## Findings during the build
- The OCR engine's own confidence barely separates misread text from correct text (misread GSTINs scored 0.84 to 0.88), so the OCR-confidence rule is weak. The checks that matter on scans are the GSTIN checksum, the sums and the verbatim check. Enlarging low-resolution scans before OCR was tried and made number recall worse (91.7% to 86.4%), so it was not kept.
- Per layout, the Hindi-English one (small Segoe text, tight spacing) lost the most characters (87% and 93% on its two scans, spaces dropped between words); the monospaced ERP layout dropped a quantity and misread a decimal point.
- A planted "valid GSTIN of the wrong company" cannot be caught by any local rule. It is the main reason a person must stay in the loop, and it is in the eval so the report shows it.

## Handover
- Operator: `docs/runbook.md` (start, review, export, when something goes wrong).
- Re-run the eval after any change to the prompt, schema, rules, thresholds, data or model.
- Next steps if the pilot proceeds: GSTIN lookup against the portal, mixed GST rates, real scans from the inbox to replace the synthetic ones, a scan-quality warning at upload.

## Review round (2026-11-10)
An independent review found real gaps before the live run. What changed:
- **Metrics.** "Straight-through" was replaced by document outcomes (auto-accepted, correct and bad straight-through, touched, planted passed silently). The baseline now shares the pipeline's extraction (repair round included) so the comparison isolates validation. The denominator is a fixed set of ground-truth cells; a flagged line list flags every line cell; lines are scored by position and by content side by side; a numeric field holding a string is wrong; each expected flag records whether the expected rule hit it.
- **Checks that would have let wrong values through.** The buyer's GSTIN or name as the seller; a tax type that does not fit the supplier's state; an invoice number that is a PO number; a due date that is not next to a due label; truncated names and ids (the source check now matches as strictly as the scorer); a table row without its numbers.
- **Robustness.** NaN and Infinity from the model, absurd numbers, oversized strings; a crash on one eval document no longer stops the run; bad model output is a 4xx; PDF image sizes are read before decoding, with caps on images per page and pixels; a foreign `Origin` on a POST is refused.
- **Product.** A document with nothing flagged is `auto_accepted` and exported by default; an uncertain document type counts as a human touch.
- **Left as known limits** (README): quantity and rate swapped, lines in the wrong order, the injection detector being a heuristic, and no authentication on the local page.
