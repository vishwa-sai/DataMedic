"""Treatment plan: validation and resolution against a job's analysis.

The resolved plan is what BOTH preview and final processing execute, so they
cannot drift apart.
"""
from __future__ import annotations

from . import config
from .errors import DataMedicError

STRATEGIES = ("preserve", "drop_rows", "fill_text", "fill_zero", "mean", "median", "mode")
NUMERIC_STRATEGIES = ("fill_zero", "mean", "median")

DEFAULT_PLAN = {
    "trim": True,
    "collapse_spaces": False,
    "normalize_columns": True,
    "remove_duplicates": True,
    "placeholders_as_missing": False,
    "drop_empty_columns": False,
    "missing": {"strategy": "preserve", "text": "", "columns": None},
    "numeric": {"convert": True, "columns": None},
    "dates": {"normalize": True, "columns": None, "order": "auto"},
    "emails": {"lowercase": False},
    "output_format": "csv",
}


def _bool(d: dict, key: str, default: bool) -> bool:
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise DataMedicError(f"Option “{key}” must be true or false.", "bad_plan", 400)
    return v


def _cols(v, ncols: int, name: str):
    if v is None:
        return None
    if not isinstance(v, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in v):
        raise DataMedicError(f"“{name}” must be a list of column numbers.", "bad_plan", 400)
    if any(i < 0 or i >= ncols for i in v):
        raise DataMedicError(f"“{name}” refers to a column that does not exist.", "bad_plan", 400)
    return sorted(set(v))


def resolve_plan(raw: dict | None, analysis: dict) -> dict:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise DataMedicError("The treatment plan is invalid.", "bad_plan", 400)
    cols = analysis["columns"]
    ncols = len(cols)
    rows = analysis["summary"]["rows"]
    notes: list[str] = []

    def sub(key):
        v = raw.get(key, {})
        if not isinstance(v, dict):
            raise DataMedicError(f"Option “{key}” is invalid.", "bad_plan", 400)
        return v

    d = DEFAULT_PLAN
    p = {
        "trim": _bool(raw, "trim", d["trim"]),
        "collapse_spaces": _bool(raw, "collapse_spaces", d["collapse_spaces"]),
        "normalize_columns": _bool(raw, "normalize_columns", d["normalize_columns"]),
        "remove_duplicates": _bool(raw, "remove_duplicates", d["remove_duplicates"]),
        "placeholders_as_missing": _bool(raw, "placeholders_as_missing", d["placeholders_as_missing"]),
        "drop_empty_columns": _bool(raw, "drop_empty_columns", d["drop_empty_columns"]),
    }
    empty_cols = set(analysis["summary"]["empty_columns"])
    p["dropped_columns"] = sorted(empty_cols) if p["drop_empty_columns"] else []
    kept = [i for i in range(ncols) if i not in p["dropped_columns"]]
    if not kept:
        raise DataMedicError("Removing empty columns would leave no columns.", "bad_plan", 400)
    p["kept_columns"] = kept

    # ---- missing values -------------------------------------------------
    m = sub("missing")
    strategy = m.get("strategy", "preserve")
    if strategy not in STRATEGIES:
        raise DataMedicError("Unknown missing-value strategy.", "bad_plan", 400)
    text = m.get("text", "")
    if not isinstance(text, str):
        raise DataMedicError("The fill text must be text.", "bad_plan", 400)
    if strategy == "fill_text":
        if text.strip() == "":
            raise DataMedicError("Enter the text to fill missing values with.", "bad_plan", 400)
        if len(text) > 500:
            raise DataMedicError("The fill text is limited to 500 characters.", "bad_plan", 400)
    scope = _cols(m.get("columns"), ncols, "missing.columns")
    scope = [i for i in (scope if scope is not None else kept) if i in kept]
    p["missing_strategy"], p["missing_text"], p["missing_columns"] = strategy, text, scope
    if strategy != "preserve" and not scope:
        raise DataMedicError("Select at least one column for the missing-value treatment.", "bad_plan", 400)

    # ---- numeric --------------------------------------------------------
    n = sub("numeric")
    convert = _bool(n, "convert", True)
    cand = [c["index"] for c in cols if c["numeric"]["candidate"] and c["index"] in kept]
    chosen = _cols(n.get("columns"), ncols, "numeric.columns")
    if chosen is None:
        p["numeric_columns"] = cand if convert else []
    else:
        bad = [i for i in chosen if i not in cand]
        if bad and convert:
            notes.append("Column(s) " + ", ".join(cols[i]["name"] or f"#{i + 1}" for i in bad) +
                         " cannot be safely converted to numbers and were skipped.")
        p["numeric_columns"] = [i for i in chosen if i in cand] if convert else []

    # ---- dates ----------------------------------------------------------
    dd = sub("dates")
    normalize = _bool(dd, "normalize", True)
    order_choice = dd.get("order", "auto")
    if order_choice not in ("auto", "dmy", "mdy"):
        raise DataMedicError("Date order must be auto, dmy or mdy.", "bad_plan", 400)
    p["date_order_choice"] = order_choice
    dchosen = _cols(dd.get("columns"), ncols, "dates.columns")
    date_cols: dict[int, dict] = {}
    skipped_dates: dict[int, str] = {}
    if normalize:
        for c in cols:
            i, dinfo = c["index"], c["date"]
            if not dinfo["candidate"] or i not in kept or (dchosen is not None and i not in dchosen):
                continue
            order = dinfo["order"]
            if order == "ambiguous":
                if order_choice in ("dmy", "mdy"):
                    order = order_choice
                else:
                    skipped_dates[i] = "day/month order is ambiguous (choose Day first or Month first)"
                    continue
            elif order == "conflict":
                skipped_dates[i] = "it mixes day-first and month-first dates"
                continue
            date_cols[i] = {"order": order, "with_time": dinfo["with_time"]}
    p["numeric_requested"] = convert
    p["dates_requested"] = normalize
    p["date_columns"] = date_cols
    p["date_skipped"] = skipped_dates

    # ---- emails ---------------------------------------------------------
    p["email_columns"] = [c["index"] for c in cols if c["email"]["is_email"] and c["index"] in kept]
    p["email_lowercase"] = _bool(sub("emails"), "lowercase", False)

    fmt = raw.get("output_format", "csv")
    if fmt not in ("csv", "xlsx"):
        raise DataMedicError("Output format must be csv or xlsx.", "bad_plan", 400)
    if fmt == "xlsx" and (rows > config.XLSX_MAX_ROWS or len(kept) > 16384):
        raise DataMedicError(
            f"This dataset has {rows:,} rows; Excel output supports at most {config.XLSX_MAX_ROWS:,}. Use CSV output.",
            "bad_plan", 400)
    p["output_format"] = fmt
    p["notes"] = notes
    return p
