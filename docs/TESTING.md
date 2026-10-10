# Testing

Run: `python -m pytest -q` → **79 passed** (~10 s) on Python 3.13.14 / Windows.
Browser E2E: `python tests\e2e_browser.py` → **E2E OK** (Microsoft Edge 154 via Playwright).

## Coverage matrix (requirement → test)

| Requirement | Test(s) |
|---|---|
| Valid small CSV / XLSX / XLS | `test_valid_small_csv`, `test_valid_xlsx_and_xls_samples`, `test_xlsx_and_csv_twins_clean_identically…` |
| Empty file, unsupported type, no file | `test_empty_file_rejected`, `test_unsupported_extension`, `test_no_file` |
| Malformed CSV, unbalanced quote, header-only | `test_malformed_csv`, `test_unbalanced_quote_is_reported…`, `test_header_only_csv_fails_clearly` |
| Encoding issues | `test_cp1252_file_is_decoded_with_warning`, `test_binary_masquerading_as_csv` |
| Fake/corrupt workbooks, zip-bomb guard | `test_fake_xlsx_and_fake_xls`, `test_corrupt_xlsx_zip`, `test_workbook_zip_bomb_guard` |
| 500 MB limit constant everywhere | `test_limit_is_exactly_500_mb_everywhere`, `test_config_endpoint_reports_limit` |
| Limit boundary (at / +1 byte / far above) | `test_size_limit_boundary` (same code path, tiny limit) **and** `scripts/limit_check.py` (real 524,288,000 / 524,288,001 bytes — measured, see below) |
| Missing values, every strategy | `test_numeric_missing_strategies[preserve,fill_zero,mean,median,mode]`, `test_fill_text_and_scope_columns`, `test_drop_rows_to_empty_output`, `test_mean_skips_non_numeric…`, `test_whitespace_only_cells_are_missing` |
| Duplicates (exact vs after trim, exact counts) | `test_diagnosis_exact_duplicates_vs_trimmed`, `test_duplicate_count_not_inferred_from_row_difference`, `test_default_plan_end_to_end` |
| Whitespace, blank cells | `test_trim_off_keeps_whitespace…`, `test_collapse_spaces_and_empty_columns` |
| Duplicate / blank / odd column names | `test_column_names_blank_duplicate_and_symbols`, `test_names_kept_when_normalization_off` |
| Dates in many formats, ambiguity, conflicts, invalid | `test_dates_all_formats_and_invalid`, `test_month_first_evidence`, `test_ambiguous_dates_skipped…`, `test_conflicting_date_orders…`, `test_datetime_column_keeps_time` |
| Numeric-looking strings, protected identifiers | `test_numeric_conversion_and_protection`, `test_mixed_numeric_column_is_not_converted` |
| Invalid emails | `test_email_validation_report_and_lowercase`, diagnosis test |
| Combined treatments | `test_combined_treatments_report_matches_output` (report ≡ output) |
| Empty output after row removal | `test_drop_rows_to_empty_output` (+ download still works) |
| Report correctness | `test_combined_treatments…`, `test_downloads_after_completion` |
| Downloads only when ready | `test_downloads_blocked_until_ready`, `test_failure_then_retry` |
| Preview accuracy / regression | `test_preview_matches_final_exactly`, `test_preview_flags_missing_row_drops…`, `test_preview_with_statistics_strategy_is_exact`, `test_large_file_preview_is_labelled_as_sample` |
| Repeated requests, state machine | `test_state_machine_blocks_invalid_transitions`, `test_reprocessing_replaces_old_result` |
| Simultaneous job isolation | `test_simultaneous_jobs_are_isolated` (5 parallel jobs) |
| Failure & retry; cancel; delete; expiry; restart | `test_failure_then_retry`, `test_failed_analysis_can_be_retried`, `test_cancel_processing`, `test_delete_job_removes_files`, `test_expired_jobs_are_cleaned_up`, `test_restart_marks_interrupted_jobs_failed` |
| Chunk-boundary correctness | `test_chunk_boundaries_do_not_change_results`, `test_median_chunked_matches_unchunked` (7-row / 5-row chunks vs one chunk) |
| Original never modified | `test_original_is_never_modified` |
| Security | path traversal, hostile filename, XSS in report, security headers, no internals in errors |
| Expected outputs of samples | `test_samples_expected.py` (5 reviewed expected CSVs in `samples/expected`) |
| Whole UI flow | `tests/e2e_browser.py` |

## Measured large-file results (real runs, this machine)
See `docs/BENCHMARKS.md` and `docs/LIMIT_CHECK_RESULT.json`. Summary: exact 500 MB file accepted, analysed and cleaned with 383 MB peak server memory;
500 MB + 1 byte rejected with HTTP 413.

## NOT tested / caveats (stated plainly)
* Only Windows + Python 3.13.14 were exercised. Linux/macOS should work but were not run.
* Only synthetic large data was used; **no real 500 MB XLSX** was processed (generating one takes very long). XLSX streaming was tested with small workbooks and the zip-bomb guard with a lowered limit.
* Legacy `.xls` was tested with files written by `xlwt` (not by real Excel). Encrypted workbooks are rejected via library errors but no encrypted sample was tested.
* Browser testing covered Microsoft Edge (Chromium) at desktop and 390 px width, not Firefox/Safari, and no screen-reader testing was done (ARIA roles/labels, keyboard focus and `prefers-reduced-motion` are implemented).
* Upload of the full 500 MB file was done from the same machine (loopback), so the 3 s upload time is not representative of a real network.
* Memory use for `median` on very large datasets and for wide (≫ 100 columns) files was reasoned about, not benchmarked.

## Manual checklist (≈ 10 minutes before presenting)
1. `python run.py --open` — page loads, stepper shows 3 steps, "Analyze" disabled with no file.
2. Drop `samples\messy_customers.csv` → name/size/type shown → Analyze → upload bar moves → lands on Medic Station.
3. Metrics read: rows 10, columns 9, missing 19, duplicates 1, invalid emails 2.
4. Diagnosis lists confirmed / likely / suggestion groups; "Go to treatment" buttons focus the right control.
5. Preview tab: removed duplicate row is struck through and labelled; changed cells show red → green; toggle *Side by side*.
6. Change strategy to *Fill with text*, type `Unknown` → preview refreshes (stale dimming then update).
7. Choose *Fill numeric with mean* on a file with numeric gaps → warning text changes.
8. Press **Done & Prepare Download** → export view with running timer and stage list.
9. When complete: three download buttons are enabled; open the CSV in Excel/Notepad and the HTML report.
10. **Back to review** keeps the completed banner; refresh the page → still on the completed job.
11. Upload `samples\malformed.csv`, `empty.csv`, a `.txt` file and a >500 MB file → each shows a clear message with Retry where sensible.
12. Upload `samples\messy_customers.xlsx` and `.xls` → same diagnosis (8 columns: trailing blank column dropped).
13. Narrow the browser to phone width → layout stacks, nothing overflows except scrollable tables.
14. Press Tab through the page → focus ring visible everywhere; Enter/Space on the drop zone opens the file picker.
