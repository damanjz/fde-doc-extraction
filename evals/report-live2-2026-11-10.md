# Eval report

Generated 2026-11-10 05:19. Model: `fusion27b:iq3`. 50 documents (40 invoices incl. 10 scans, 10 delivery notes). Fresh SQLite file, fixed document order.

## Headline

Silent error rate = wrong fields that were not flagged / all ground-truth fields. These are the errors that reach the spreadsheet.

| Variant | Field accuracy | Silent error rate | Docs with a silent error | Auto-accepted | Correct straight-through | Bad straight-through | Touched by a human | Planted problems passed silently |
|---|---|---|---|---|---|---|---|---|
| Pipeline (validation + confidence) | 99.2% | 0.0% (0/1130) | 0/50 | 32 | 32 | 0 | 18 | 0/13 |
| Baseline (raw extraction) | 99.2% | 0.8% (9/1130) | 5/50 | 50 | 33 | 17 | 0 | 13/13 |

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
| Invoices, text PDF | 30 | 800 | 99.9% | 0.0% | 0.1% | 21 / 0 / 9 | 21 / 9 / 0 |
| Invoices, scans (OCR) (optimistic: clean synthetic scans) | 10 | 200 | 96.0% | 0.0% | 4.0% | 3 / 0 / 7 | 4 / 6 / 0 |
| Delivery notes, text PDF | 10 | 130 | 100.0% | 0.0% | 0.0% | 8 / 0 / 2 | 8 / 2 / 0 |

Scans are generated from clean synthetic renders, so the OCR numbers here are optimistic compared with real phone or flatbed scans.

## Line items: two ways to align

The headline matches predicted lines to true lines by position, as the spec says: one dropped or split line shifts every line after it. Content alignment matches each true line to the predicted line with the same amount or description, so it shows how much of the line damage is only an order or offset problem.

| Alignment | Line cells correct |
|---|---|
| By position (used in the headline) | 594/600 (99.0%) |
| By content | 594/600 (99.0%) |

## Flag quality (pipeline)

- Recall: 9/9 (100.0%) of wrong fields were flagged.
- Precision: 9/33 (27.3%) of flagged fields were wrong.
- Cells of planted problems are left out of both (they are correctly extracted values that should be flagged; scored below). Documents the pipeline failed on are left out too. When the line list is flagged as a whole, every line cell counts as flagged.

Per document: 17/18 (94.4%) of the documents sent to a person really had a problem (a wrong field, a planted problem or an expected warning); 17/17 (100.0%) of the documents with a problem were sent.

Why correct fields were flagged (false alarms):

| Reason | Fields |
|---|---|
| line list flagged | 15 |
| ocr_low_confidence | 5 |
| validation:line_incomplete | 3 |
| not_in_source | 1 |

No silent errors.

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
| invoice_date | 40 | 100.0% | 0 | 0 |
| invoice_number | 40 | 100.0% | 0 | 0 |
| line_items.amount | 150 | 99.3% | 1 | 0 |
| line_items.description | 150 | 99.3% | 1 | 0 |
| line_items.qty | 150 | 98.0% | 3 | 0 |
| line_items.unit_price | 150 | 99.3% | 1 | 0 |
| origin | 10 | 100.0% | 0 | 0 |
| packages | 10 | 100.0% | 0 | 0 |
| received_by | 10 | 100.0% | 0 | 0 |
| reference_number | 10 | 100.0% | 0 | 0 |
| ship_date | 10 | 100.0% | 0 | 0 |
| subtotal | 40 | 100.0% | 0 | 0 |
| supplier_gstin | 40 | 95.0% | 2 | 0 |
| supplier_name | 40 | 100.0% | 0 | 0 |
| total | 40 | 100.0% | 0 | 0 |
| weight_kg | 10 | 100.0% | 0 | 0 |

## Latency per document

| Variant | p50 | p95 |
|---|---|---|
| Pipeline (text layer or OCR + LLM + checks) | 9.1s | 12.1s |
| Baseline (text layer or OCR + LLM, no checks) | 9.1s | 12.1s |

- Invoices, text PDF: p50 9.1s, p95 19.9s
- Invoices, scans (OCR): p50 10.3s, p95 12.1s
- Delivery notes, text PDF: p50 6.3s, p95 6.5s

## Other

- Document type recognised correctly: 50/50 (100.0%).
- Failed extractions: 0.
- First reply was not valid JSON and the repair round rescued it: 0.
