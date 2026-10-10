"""Quality report generation (JSON + self-contained HTML). All cell values are
untrusted, so everything interpolated into HTML is escaped."""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path


def build_report(job: dict, analysis: dict, plan: dict, result: dict, out_name: str, out_size: int,
                 started: float, finished: float) -> dict:
    m = result["metrics"]
    cols = analysis["columns"]
    ops = result["operations"]
    return {
        "tool": "DataMedic — Intelligent Data Quality & Repair",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "job_id": job["id"],
        "file": {
            "original_name": job["original_name"], "original_size_bytes": job["size"],
            "source_type": analysis["file"].get("kind"), "sheet": analysis["file"].get("sheet"),
            "encoding": analysis["file"].get("encoding"),
            "output_name": out_name, "output_size_bytes": out_size, "output_format": plan["output_format"],
        },
        "summary": {
            "rows_before": m["rows_in"], "rows_after": m["rows_out"],
            "columns_before": m["cols_in"], "columns_after": m["cols_out"],
            "missing_cells_before": m["missing_before"], "missing_cells_after": m["missing_after"],
            "duplicate_rows_removed": m["duplicates_removed"],
            "rows_removed_for_missing_values": m["rows_dropped_missing"],
            "missing_values_filled": m["missing_filled"],
            "cells_trimmed": m["cells_trimmed"], "cells_whitespace_collapsed": m["cells_collapsed"],
            "placeholders_converted_to_missing": m["placeholders_nulled"],
            "numeric_values_reformatted": m["numeric_converted"],
            "numeric_columns_converted": m["numeric_columns_converted"],
            "dates_normalized": m["dates_normalized"], "dates_left_unparsed": m["dates_unparsed"],
            "date_columns_normalized": m["date_columns_normalized"],
            "invalid_emails_found": m["emails_invalid"], "emails_lowercased": m["emails_lowercased"],
            "columns_renamed": m["columns_renamed"], "columns_removed": m["columns_removed"],
        },
        "operations": ops,
        "requested_vs_applied": {
            "requested": [o["key"] for o in ops if o["requested"]],
            "applied": [o["key"] for o in ops if o["status"] == "applied"],
            "skipped": [o["key"] for o in ops if o["status"] == "skipped"],
        },
        "treatment_settings": {k: v for k, v in plan.items() if k not in ("notes",)},
        "columns": result["columns"],
        "validation": {"invalid_email_samples": result["email_samples"]},
        "warnings": result["warnings"],
        "processing": {
            "duration_seconds": round(finished - started, 2),
            "engine_seconds": m["processing_seconds"],
            "started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(timespec="seconds"),
            "finished_at": dt.datetime.fromtimestamp(finished, dt.timezone.utc).isoformat(timespec="seconds"),
        },
        "notes": [
            "Order of operations: trim, collapse spaces, placeholders, numeric, dates, email case, "
            "duplicate removal, then the missing-value strategy.",
            "Duplicates are counted at the moment they are removed, not inferred from row-count differences.",
            "The original uploaded file was not modified.",
        ],
    }


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _n(v) -> str:
    return f"{v:,}" if isinstance(v, int) else _e(v)


def _bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


_CSS = """body{font:15px/1.5 system-ui,Segoe UI,sans-serif;color:#10222b;margin:0;background:#f4f8f8}
main{max-width:1000px;margin:0 auto;padding:32px 20px}h1{margin:0 0 4px}h2{margin-top:32px;border-bottom:2px solid #0f766e;padding-bottom:4px}
.sub{color:#51676f}table{border-collapse:collapse;width:100%;background:#fff;margin:10px 0}
th,td{border:1px solid #d5e1e3;padding:6px 10px;text-align:left;vertical-align:top;font-size:14px}th{background:#e6f2f1}
td.n{text-align:right;font-variant-numeric:tabular-nums}.ok{color:#0f766e;font-weight:600}.sk{color:#b45309;font-weight:600}
.warn{background:#fff7e6;border-left:4px solid #d97706;padding:8px 12px;margin:6px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.card{background:#fff;border:1px solid #d5e1e3;border-radius:8px;padding:12px}.card b{display:block;font-size:22px}"""


def render_html(rep: dict) -> str:
    f, s = rep["file"], rep["summary"]
    cards = [
        ("Rows", f"{_n(s['rows_before'])} → {_n(s['rows_after'])}"),
        ("Columns", f"{_n(s['columns_before'])} → {_n(s['columns_after'])}"),
        ("Missing cells", f"{_n(s['missing_cells_before'])} → {_n(s['missing_cells_after'])}"),
        ("Duplicates removed", _n(s["duplicate_rows_removed"])),
        ("Rows removed (missing)", _n(s["rows_removed_for_missing_values"])),
        ("Values filled", _n(s["missing_values_filled"])),
        ("Dates normalized", _n(s["dates_normalized"])),
        ("Numbers reformatted", _n(s["numeric_values_reformatted"])),
        ("Invalid emails found", _n(s["invalid_emails_found"])),
        ("Duration", f"{rep['processing']['duration_seconds']} s"),
    ]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>DataMedic report</title>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{_CSS}</style></head><body><main>",
           "<h1>DataMedic quality report</h1>",
           f"<p class='sub'>{_e(f['original_name'])} → {_e(f['output_name'])} · generated {_e(rep['generated_at'])} · job {_e(rep['job_id'])}</p>",
           "<div class='grid'>" + "".join(f"<div class='card'>{_e(k)}<b>{v}</b></div>" for k, v in cards) + "</div>",
           "<h2>Files</h2><table>",
           f"<tr><th>Original</th><td>{_e(f['original_name'])} ({_e(_bytes(f['original_size_bytes']))}, {_e(f['source_type'])})</td></tr>",
           f"<tr><th>Cleaned output</th><td>{_e(f['output_name'])} ({_e(_bytes(f['output_size_bytes']))}, {_e(f['output_format'])})</td></tr></table>",
           "<h2>Operations requested and applied</h2><table><tr><th>Operation</th><th>Status</th><th>Affected</th><th>Details</th></tr>"]
    label = {"applied": "<span class='ok'>Applied</span>", "skipped": "<span class='sk'>Skipped</span>",
             "not_requested": "Not requested"}
    for o in rep["operations"]:
        out.append(f"<tr><td>{_e(o['label'])}</td><td>{label[o['status']]}</td><td class='n'>{_n(o['affected'])}</td><td>{_e(o['detail'])}</td></tr>")
    out.append("</table>")
    if rep["warnings"]:
        out.append("<h2>Warnings and skipped items</h2>" + "".join(f"<div class='warn'>{_e(w)}</div>" for w in rep["warnings"]))
    em = rep["validation"]["invalid_email_samples"]
    if em:
        out.append("<h2>Email validation</h2><table><tr><th>Column</th><th>Data row</th><th>Invalid value</th></tr>")
        names = {str(c["index"]): c["original_name"] for c in rep["columns"]}
        for idx, rows in em.items():
            for r in rows:
                out.append(f"<tr><td>{_e(names.get(idx))}</td><td class='n'>{_n(r['row'])}</td><td>{_e(r['value'])}</td></tr>")
        out.append("</table><p class='sub'>Up to 10 examples per column; invalid values are reported, never deleted.</p>")
    out.append("<h2>Per-column detail</h2><table><tr><th>Original</th><th>Output name</th><th>Missing before</th><th>Missing after</th>"
               "<th>Filled</th><th>Trimmed</th><th>Numbers</th><th>Dates</th><th>Dates unparsed</th><th>Invalid emails</th></tr>")
    for c in rep["columns"]:
        out.append("<tr>" + "".join(f"<td{' class=n' if i > 1 else ''}>{_n(v) if i > 1 else _e(v)}</td>" for i, v in enumerate([
            c["original_name"] or "(blank)", c["output_name"] or "(removed)", c["missing_before"],
            "" if c["missing_after"] is None else c["missing_after"], c["filled"], c["trimmed"],
            c["numeric_converted"], c["dates_normalized"], c["dates_unparsed"], c["emails_invalid"]])) + "</tr>")
    out.append("</table><h2>Notes</h2><ul>" + "".join(f"<li>{_e(n)}</li>" for n in rep["notes"]) + "</ul>")
    out.append(f"<p class='sub'>Processing started {_e(rep['processing']['started_at'])}, finished {_e(rep['processing']['finished_at'])}.</p>")
    out.append("</main></body></html>")
    return "".join(out)


def write_reports(out_dir: Path, rep: dict) -> None:
    (out_dir / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.html").write_text(render_html(rep), encoding="utf-8")
