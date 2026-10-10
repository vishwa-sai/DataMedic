# Security and error-handling notes

Uploaded files are treated as **untrusted input**.

| Risk | Mitigation (where) |
|---|---|
| Oversized upload | 500 MB limit at browser, Flask, Waitress and exact post-save byte check; rejected uploads are deleted (`test_size_limit_boundary`, `scripts/limit_check.py`) |
| Wrong/renamed file types | extension allow-list + magic-byte checks (zip header for `.xlsx`, OLE header for `.xls`, NUL-byte check for CSV) (`ingest.validate_upload`) |
| Zip bombs / huge workbooks | uncompressed-size cap (2 GB), compression-ratio check, column cap, conversion time limit, `.xls` ≤ 100 MB, streaming read-only parsing |
| Path traversal | job ids must match `^[0-9a-f]{32}$`; on-disk names are server-generated; downloads only from a fixed whitelist of paths inside the job folder (`test_path_traversal_and_bad_ids`, `test_hostile_filename_is_never_used_on_disk`) |
| Code execution | uploaded content is only ever parsed as data; nothing is `eval`-ed or imported; formulas are read as cached values |
| Formula injection in XLSX output | text cells are written with `data_type="s"` so `=…` stays text (`test_xlsx_output_is_typed_and_verified`). In **CSV** output values are never altered; opening a CSV in Excel can still evaluate cells that start with `=`, `+`, `-`, `@` — import via *Data → From Text/CSV* if the source is untrusted |
| XSS | UI uses `textContent`/DOM nodes only; HTML report escapes every value (`test_html_report_escapes_untrusted_values`); CSP `script-src 'self'; style-src 'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` |
| Information leakage | domain errors carry short user-safe messages; unexpected exceptions are logged to `instance/datamedic.log` with a job reference only and returned as a generic message (`test_failure_then_retry`) |
| Cross-job interference | per-job folders and in-memory records; plan, outputs and downloads are always looked up by job id; simultaneous-job test |
| Resource exhaustion | bounded workers/previews, chunked processing, caps for median/mode memory, processing timeout, cooperative cancel, 24 h cleanup |
| Data loss | the uploaded source is never modified; output goes to a new file; old results are only removed when a new run starts |

**Not provided (by design for a hackathon build):** authentication, per-user isolation, HTTPS, rate limiting, virus scanning.
Anyone who can reach the port can upload files. Run on `127.0.0.1` (default) or put it behind an authenticating HTTPS reverse
proxy before exposing it. Job data lives in plain files under `instance\` until deleted (button "Start over", or after 24 h).

## Error handling summary
Every failure path ends in a `failed` job (or HTTP error) with a plain-language message and a retry path in the UI:
empty file, unsupported type, fake/corrupt workbook, malformed CSV (with the offending line), undecodable text,
header-only file, too many columns, workbook too large, out-of-memory, timeout, cancelled, server restart,
verification mismatch (the result is discarded, never offered for download), unexpected exception.
