# Eval report (MOCK RUN)

> **This run used a mocked LLM** that returns the ground truth with a fixed list of injected errors. It proves the scoring works. It says nothing about the model. The live numbers are in `evals/report.md`.

Generated 2026-11-10 04:35. Model: `mock`. 50 documents (40 invoices incl. 10 scans, 10 delivery notes). Fresh SQLite file, fixed document order.

## Headline

Silent error rate = wrong fields that were not flagged / all ground-truth fields. These are the errors that reach the spreadsheet.

| Variant | Field accuracy | Silent error rate | Docs with a silent error | Auto-accepted | Correct straight-through | Bad straight-through | Touched by a human | Planted problems passed silently |
|---|---|---|---|---|---|---|---|---|
| Pipeline (validation + confidence) | 98.0% | 0.9% (10/1130) | 2/50 | 26 | 24 | 2 | 24 | 0/13 |
| Baseline (raw extraction) | 98.0% | 2.0% (23/1130) | 12/50 | 50 | 25 | 25 | 0 | 13/13 |

Document outcomes:

- **Auto-accepted**: nothing for a person to look at (no flagged field, no document warning; a document type that was a guess counts as a human touch).
- **Correct straight-through**: auto-accepted, every field right, no planted problem.
- **Bad straight-through**: auto-accepted with a wrong field or a planted problem in it. This is what hurts.
- **Touched by a human**: flagged, warned about, or failed.
- **Planted problems passed silently**: planted documents that were auto-accepted.

The baseline is the same extraction as the pipeline (same prompt, same reply, repair round included, same normalisation) without validation, confidence or warnings. Validation never changes a value, so field accuracy is identical by construction and the two differ only in what is flagged. Every wrong field of the baseline is silent and every document it extracts is auto-accepted.

## By document group

| Group | Docs | Fields | Accuracy | Silent err, pipeline | Silent err, baseline | Pipeline: correct STP / bad STP / touched | Baseline: correct STP / bad STP / touched |
|---|---|---|---|---|---|---|---|
| Invoices, text PDF | 30 | 800 | 97.6% | 1.2% | 2.4% | 14 / 2 / 14 | 14 / 16 / 0 |
| Invoices, scans (OCR) (optimistic: clean synthetic scans) | 10 | 200 | 98.5% | 0.0% | 1.5% | 3 / 0 / 7 | 4 / 6 / 0 |
| Delivery notes, text PDF | 10 | 130 | 99.2% | 0.0% | 0.8% | 7 / 0 / 3 | 7 / 3 / 0 |

Scans are generated from clean synthetic renders, so the OCR numbers here are optimistic compared with real phone or flatbed scans.

## Line items: two ways to align

The headline matches predicted lines to true lines by position, as the spec says: one dropped or split line shifts every line after it. Content alignment matches each true line to the predicted line with the same amount or description, so it shows how much of the line damage is only an order or offset problem.

| Alignment | Line cells correct |
|---|---|
| By position (used in the headline) | 584/600 (97.3%) |
| By content | 592/600 (98.7%) |

## Flag quality (pipeline)

- Recall: 13/23 (56.5%) of wrong fields were flagged.
- Precision: 13/71 (18.3%) of flagged fields were wrong.
- Cells of planted problems are left out of both (they are correctly extracted values that should be flagged; scored below). Documents the pipeline failed on are left out too. When the line list is flagged as a whole, every line cell counts as flagged.

Per document: 23/24 (95.8%) of the documents sent to a person really had a problem (a wrong field, a planted problem or an expected warning); 23/25 (92.0%) of the documents with a problem were sent.

Why correct fields were flagged (false alarms):

| Reason | Fields |
|---|---|
| line list flagged | 40 |
| not_in_source | 5 |
| ocr_low_confidence | 5 |
| validation:subtotal_mismatch | 2 |
| validation:line_amount_mismatch | 2 |
| validation:tax_mismatch | 2 |
| validation:total_mismatch | 2 |
| validation:line_incomplete | 1 |

Wrong fields that were not flagged (silent errors):

| Doc | Field | Truth | Extracted |
|---|---|---|---|
| inv_013 | line_items[0].qty | 139.0 | 863.0 |
| inv_013 | line_items[0].unit_price | 863.0 | 139.0 |
| inv_018 | line_items[0].description | Cooling tray, stainless steel, 40 cm | Chaff collector assembly |
| inv_018 | line_items[0].qty | 4.0 | 1.0 |
| inv_018 | line_items[0].unit_price | 5421.0 | 11919.0 |
| inv_018 | line_items[0].amount | 21684.0 | 11919.0 |
| inv_018 | line_items[1].description | Chaff collector assembly | Cooling tray, stainless steel, 40 cm |
| inv_018 | line_items[1].qty | 1.0 | 4.0 |
| inv_018 | line_items[1].unit_price | 11919.0 | 5421.0 |
| inv_018 | line_items[1].amount | 11919.0 | 21684.0 |

## Planted problems

Expected flags: **12/12 flagged by the expected rule**, 0 flagged for another reason, 0 missed, 0 on documents that failed. Every expected flag was raised.

| Doc | Field | Problem | Flagged | Reasons on the field |
|---|---|---|---|---|
| dn_005 | delivery_date | delivery_before_ship | yes | validation:delivery_before_ship |
| dn_009 | courier_gstin | gstin_checksum | yes | validation:gstin_checksum |
| inv_005 | supplier_gstin | gstin_checksum | yes | validation:gstin_checksum |
| inv_008 | line_items[2].amount | line_amount_mismatch | yes | validation:line_amount_mismatch |
| inv_011 | invoice_date | missing | yes | validation:missing |
| inv_014 | supplier_gstin | gstin_checksum | yes | validation:gstin_checksum |
| inv_017 | total | total_mismatch | yes | validation:total_mismatch |
| inv_022 | invoice_date | missing | yes | validation:missing |
| inv_023 | invoice_number | duplicate_invoice_number | yes | validation:duplicate_invoice_number |
| scan_002 | invoice_number | duplicate_invoice_number | yes | validation:duplicate_invoice_number |
| scan_004 | subtotal | subtotal_mismatch | yes | validation:subtotal_mismatch |
| scan_008 | supplier_gstin | gstin_checksum | yes | validation:gstin_checksum |

- inv_019: document warning `instruction_in_document` raised; all extracted values correct.

## Accuracy per field

| Field | Cells | Accuracy | Wrong and flagged | Wrong and silent |
|---|---|---|---|---|
| awb_number | 10 | 100.0% | 0 | 0 |
| consignee | 10 | 100.0% | 0 | 0 |
| consignor | 10 | 100.0% | 0 | 0 |
| courier_gstin | 10 | 100.0% | 0 | 0 |
| courier_name | 10 | 100.0% | 0 | 0 |
| currency | 40 | 100.0% | 0 | 0 |
| delivery_date | 10 | 100.0% | 0 | 0 |
| destination | 10 | 100.0% | 0 | 0 |
| due_date | 40 | 97.5% | 1 | 0 |
| gst_amount | 40 | 100.0% | 0 | 0 |
| gst_rate | 40 | 100.0% | 0 | 0 |
| invoice_date | 40 | 97.5% | 1 | 0 |
| invoice_number | 40 | 100.0% | 0 | 0 |
| line_items.amount | 150 | 97.3% | 2 | 2 |
| line_items.description | 150 | 98.0% | 1 | 2 |
| line_items.qty | 150 | 96.7% | 2 | 3 |
| line_items.unit_price | 150 | 97.3% | 1 | 3 |
| origin | 10 | 100.0% | 0 | 0 |
| packages | 10 | 100.0% | 0 | 0 |
| received_by | 10 | 90.0% | 1 | 0 |
| reference_number | 10 | 100.0% | 0 | 0 |
| ship_date | 10 | 100.0% | 0 | 0 |
| subtotal | 40 | 97.5% | 1 | 0 |
| supplier_gstin | 40 | 97.5% | 1 | 0 |
| supplier_name | 40 | 97.5% | 1 | 0 |
| total | 40 | 97.5% | 1 | 0 |
| weight_kg | 10 | 100.0% | 0 | 0 |

## Latency per document

| Variant | p50 | p95 |
|---|---|---|
| Pipeline (text layer or OCR + LLM + checks) | 0.0s | 2.6s |
| Baseline (text layer or OCR + LLM, no checks) | 0.0s | 2.6s |

- Invoices, text PDF: p50 0.0s, p95 0.0s
- Invoices, scans (OCR): p50 2.5s, p95 3.0s
- Delivery notes, text PDF: p50 0.0s, p95 0.0s

Mock latencies cover text extraction, OCR and the checks only; there is no model call.

## Other

- Document type recognised correctly: 50/50 (100.0%).
- Failed extractions: 0.
- First reply was not valid JSON and the repair round rescued it: 2 (inv_002, scan_007).

## Mock: injected errors

Errors the mock injected (some are caught, some are silent on purpose):

- dn_003: received_by
- inv_004: supplier_name
- inv_007: line_items[0].amount
- inv_009: supplier_gstin
- inv_010: line_items
- inv_013: line_items
- inv_016: invoice_date
- inv_018: line_items
- inv_024: due_date
- scan_005: total
- scan_009: line_items[0].qty
- scan_010: subtotal
