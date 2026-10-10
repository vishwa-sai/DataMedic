"""Correctness of diagnosis, every treatment, and the report metrics."""
import json

import pandas as pd
import pytest

from datamedic import config

BASE = """Name ,Email,Age,Joined,Amount,Zip
 Alice ,ALICE@x.com,30,2023-01-05,"$1,200.50",01234
Bob,bob@x,,15/01/2023,950,02134
Carol,carol@x.com,40,"Jan 7, 2023","1,000",90210
Alice,ALICE@x.com,30,2023-01-05,"$1,200.50",01234
Dan,,25,2023-02-01,,10001
Dan,,25,2023-02-01,,10001
"""


def run(h, plan=None, data=BASE, name="t.csv"):
    j = h.run(name, data, plan)
    assert j["state"] == "completed", j["error"]
    fmt = (plan or {}).get("output_format", "csv")
    return j, h.out_df(j["id"], fmt) if fmt == "csv" else None, j["result"]["metrics"]


def test_diagnosis_numbers_are_exact(h):
    j = h.upload("t.csv", BASE)
    a = h.analysis(j["id"])
    s = a["summary"]
    assert s["rows"] == 6 and s["columns"] == 6
    # missing: Bob.Age, Dan.Email x2, Dan.Amount x2  -> 5
    assert s["missing_cells"] == 5
    assert s["duplicate_rows"] == 1                    # Dan is an exact duplicate
    assert s["duplicate_rows_after_trim"] == 2         # Alice only matches once whitespace is trimmed
    cols = {c["name"]: c for c in a["columns"]}
    assert cols["Zip"]["numeric"]["protected_reason"]                # leading zeros protected
    assert cols["Amount"]["numeric"]["candidate"] is True           # all non-blank values are numeric
    assert cols["Amount"]["numeric"]["needs_cleaning"] == 3         # $1,200.50 x2 and 1,000
    assert cols["Email"]["email"]["invalid"] == 1                   # bob@x
    assert cols["Joined"]["date"]["order"] == "dmy"


def test_diagnosis_exact_duplicates_vs_trimmed(h):
    j = h.upload("d.csv", "a,b\n1,x\n1,x\n1, x\n2,y\n")
    s = h.analysis(j["id"])["summary"]
    assert s["duplicate_rows"] == 1 and s["duplicate_rows_after_trim"] == 2


def test_default_plan_end_to_end(h):
    j, df, m = run(h)
    assert list(df.columns) == ["name", "email", "age", "joined", "amount", "zip"]
    assert m["rows_in"] == 6 and m["rows_out"] == 4
    assert m["duplicates_removed"] == 2                # Alice (after trim) + Dan
    assert df["name"].tolist() == ["Alice", "Bob", "Carol", "Dan"]
    assert df["zip"].tolist() == ["01234", "02134", "90210", "10001"]          # leading zeros preserved
    assert df["joined"].tolist() == ["2023-01-05", "2023-01-15", "2023-01-07", "2023-02-01"]
    assert df["amount"].tolist() == ["1200.50", "950", "1000", ""]
    assert m["missing_before"] == 5 and m["missing_after"] == 3               # Bob.age, Dan.email, Dan.amount
    assert df["email"].tolist() == ["ALICE@x.com", "bob@x", "carol@x.com", ""]  # emails untouched by default


def test_duplicate_count_not_inferred_from_row_difference(h):
    """Duplicates AND missing-row dropping both remove rows; each count must be exact."""
    data = "k,v\n1,a\n1,a\n2,\n3,c\n3,c\n4,\n"
    j, df, m = run(h, {"missing": {"strategy": "drop_rows"}}, data)
    assert m["rows_in"] == 6 and m["rows_out"] == 2
    assert m["duplicates_removed"] == 2 and m["rows_dropped_missing"] == 2
    assert m["rows_in"] - m["rows_out"] == m["duplicates_removed"] + m["rows_dropped_missing"]
    assert df["k"].tolist() == ["1", "3"]


def test_trim_off_keeps_whitespace_and_distinct_rows(h):
    j, df, m = run(h, {"trim": False, "normalize_columns": False}, "a,b\n1, x\n1,x\n")
    assert m["rows_out"] == 2 and m["cells_trimmed"] == 0
    assert df.iloc[0, 1] == " x"


@pytest.mark.parametrize("strategy,expected", [
    ("preserve", ["10", "", "30", "", "50"]),
    ("fill_zero", ["10", "0", "30", "0", "50"]),
    ("mean", ["10", "30", "30", "30", "50"]),
    ("median", ["10", "30", "30", "30", "50"]),
    ("mode", ["10", "10", "30", "10", "50"]),
])
def test_numeric_missing_strategies(h, strategy, expected):
    data = "id,val\na,10\nb,\nc,30\nd,\ne,50\n"
    plan = {"missing": {"strategy": strategy, "columns": [1]}}
    j, df, m = run(h, plan, data)
    assert df["val"].tolist() == expected
    assert m["missing_filled"] == (0 if strategy == "preserve" else 2)
    assert m["missing_after"] == (2 if strategy == "preserve" else 0)


def test_fill_text_and_scope_columns(h):
    data = "a,b\n1,\n,y\n"
    j, df, m = run(h, {"missing": {"strategy": "fill_text", "text": "Unknown", "columns": [1]}}, data)
    assert df["b"].tolist() == ["Unknown", "y"] and df["a"].tolist() == ["1", ""]
    assert m["missing_filled"] == 1


def test_fill_text_requires_text(h):
    j = h.upload("t.csv", "a\n1\n")
    r = h.c.post(f"/api/jobs/{j['id']}/process", json={"plan": {"missing": {"strategy": "fill_text", "text": "  "}}})
    assert r.status_code == 400


def test_mean_skips_non_numeric_columns_with_warning(h):
    j, df, m = run(h, {"missing": {"strategy": "mean"}}, "n,t\n1,\n,x\n3,y\n")
    assert df["n"].tolist() == ["1", "2", "3"]
    assert df["t"].tolist() == ["", "x", "y"]
    assert any("was left unchanged" in w for w in j["result"]["warnings"])
    op = next(o for o in j["result"]["operations"] if o["key"] == "missing")
    assert op["status"] == "applied" and op["affected"] == 1


def test_mean_on_all_text_is_reported_as_skipped(h):
    j, df, m = run(h, {"missing": {"strategy": "mean"}}, "t\nx\n\ny\n")
    op = next(o for o in j["result"]["operations"] if o["key"] == "missing")
    assert op["status"] == "skipped" and m["missing_filled"] == 0


def test_whitespace_only_cells_are_missing(h):
    j, df, m = run(h, {"trim": False, "missing": {"strategy": "fill_text", "text": "?"}}, "a,b\n1,   \n2,x\n")
    assert df["b"].tolist() == ["?", "x"]
    assert m["missing_before"] == 1


def test_drop_rows_to_empty_output(h):
    j, df, m = run(h, {"missing": {"strategy": "drop_rows"}}, "a,b\n,1\n2,\n")
    assert m["rows_out"] == 0 and len(df) == 0 and list(df.columns) == ["a", "b"]
    assert any("no data rows" in w for w in j["result"]["warnings"])
    assert h.c.get(f"/api/jobs/{j['id']}/download/cleaned").status_code == 200


def test_column_names_blank_duplicate_and_symbols(h):
    j, df, m = run(h, {}, "Name,name, ,Amount ($),Ünï Cödé\n1,2,3,4,5\n")
    assert list(df.columns) == ["name", "name_2", "column_3", "amount", "uni_code"]
    assert m["columns_renamed"] == 5


def test_names_kept_when_normalization_off(h):
    j, df, m = run(h, {"normalize_columns": False}, "A B,A B\n1,2\n")
    assert list(df.columns) == ["A B", "A B.1"] or list(df.columns)[0] == "A B"   # pandas re-reading mangles dupes
    raw = (h.store.job_dir(j["id"]) / "output" / "cleaned.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert raw == "A B,A B"


def test_dates_all_formats_and_invalid(h):
    iso = [f"2023-03-{d:02d}" for d in range(1, 25)]
    data = "d\n" + "\n".join(iso) + "\n05/03/2023\n13/03/2023\n5 Mar 2023\n\"March 5, 2023\"\n2023/3/5\n31/02/2023\nnot a date\n"
    j = h.upload("d.csv", data)
    a = h.analysis(j["id"])
    # 13/03 proves day-first for the whole column
    assert a["columns"][0]["date"]["order"] == "dmy"
    done = h.process(j["id"], {})
    # normalised duplicates of 2023-03-05 are removed by duplicate removal; keep them via plan
    df = h.out_df(done["id"])
    assert df["d"].tolist() == iso + ["31/02/2023", "not a date"]
    m = done["result"]["metrics"]
    # all 5 rewritten dates equal an existing ISO row, so duplicate removal (which runs AFTER
    # normalisation) removes them - and the count is exact, not inferred.
    assert m["dates_unparsed"] == 2 and m["dates_normalized"] == 5 and m["duplicates_removed"] == 5


def test_month_first_evidence(h):
    j, df, m = run(h, {}, "d\n03/13/2023\n01/02/2023\n")
    assert df["d"].tolist() == ["2023-03-13", "2023-01-02"]


def test_ambiguous_dates_skipped_unless_user_chooses(h):
    data = "d\n01/02/2023\n03/04/2023\n"
    j, df, m = run(h, {}, data)
    assert df["d"].tolist() == ["01/02/2023", "03/04/2023"] and m["dates_normalized"] == 0
    assert any("not converted" in w for w in j["result"]["warnings"])
    j2, df2, _ = run(h, {"dates": {"normalize": True, "order": "dmy"}}, data)
    assert df2["d"].tolist() == ["2023-02-01", "2023-04-03"]
    j3, df3, _ = run(h, {"dates": {"normalize": True, "order": "mdy"}}, data)
    assert df3["d"].tolist() == ["2023-01-02", "2023-03-04"]


def test_conflicting_date_orders_are_never_guessed(h):
    j, df, m = run(h, {"dates": {"order": "dmy"}}, "d\n13/01/2023\n01/13/2023\n")
    assert df["d"].tolist() == ["13/01/2023", "01/13/2023"] and m["dates_normalized"] == 0


def test_datetime_column_keeps_time(h):
    j, df, m = run(h, {}, "t\n2023-01-02T03:04:05\n2023-01-03 10:00\n2023-01-04\n")
    assert df["t"].tolist() == ["2023-01-02 03:04:05", "2023-01-03 10:00:00", "2023-01-04 00:00:00"]


def test_numeric_conversion_and_protection(h):
    data = "price,phone,zip,code,ratio\n$1,200.50,0123456789,01234,A1,+.5\n€99,0987654321,02134,B2,1e3\n10,0555123456,90210,C3,-2\n"
    j, df, m = run(h, {}, data.replace("$1,200.50", '"$1,200.50"'))
    assert df["price"].tolist() == ["1200.50", "99", "10"]
    assert df["ratio"].tolist() == ["0.5", "1e3", "-2"]
    assert df["phone"].tolist()[0] == "0123456789" and df["zip"].tolist()[0] == "01234"
    assert m["numeric_columns_converted"] == 2


def test_mixed_numeric_column_is_not_converted(h):
    j, df, m = run(h, {}, 'v\n"$1,000"\nabc\n5\n')
    assert df["v"].tolist() == ["$1,000", "abc", "5"] and m["numeric_converted"] == 0
    a = h.analysis(j["id"])
    assert any(i["id"].startswith("mixed-") for i in a["issues"])


def test_placeholders_optional(h):
    data = "v\n1\nN/A\nnull\n3\n"
    j, df, m = run(h, {"placeholders_as_missing": True, "remove_duplicates": False, "missing": {"strategy": "mean"}}, data)
    assert df["v"].tolist() == ["1", "2", "2", "3"] and m["placeholders_nulled"] == 2
    j2, df2, _ = run(h, {}, data)
    assert df2["v"].tolist() == ["1", "N/A", "null", "3"]


def test_email_validation_report_and_lowercase(h):
    data = "email\nA@B.com\nbad\nx@@y.com\nok@ok.org\n\n"
    j, df, m = run(h, {"emails": {"lowercase": True}}, data)
    assert m["emails_invalid"] == 2 and m["emails_lowercased"] == 1
    assert df["email"].tolist() == ["a@b.com", "bad", "x@@y.com", "ok@ok.org", ""][:len(df)]
    rep = json.loads((h.store.job_dir(j["id"]) / "output" / "report.json").read_text(encoding="utf-8"))
    samples = rep["validation"]["invalid_email_samples"]["0"]
    assert [s["value"] for s in samples] == ["bad", "x@@y.com"] and samples[0]["row"] == 2


def test_collapse_spaces_and_empty_columns(h):
    j, df, m = run(h, {"collapse_spaces": True, "drop_empty_columns": True}, "a,b,c\nx   y,,1\nz,,2\n")
    assert list(df.columns) == ["a", "c"] and df["a"].tolist() == ["x y", "z"]
    assert m["cols_out"] == 2 and m["columns_removed"] == 1
    assert m["missing_before"] == 2 and m["missing_after"] == 0   # before counts ALL original columns


def test_combined_treatments_report_matches_output(h):
    plan = {"missing": {"strategy": "median", "columns": [2]}, "emails": {"lowercase": True},
            "placeholders_as_missing": True}
    j, df, m = run(h, plan)
    rep = json.loads((h.store.job_dir(j["id"]) / "output" / "report.json").read_text(encoding="utf-8"))
    s = rep["summary"]
    assert s["rows_after"] == len(df) and s["columns_after"] == len(df.columns)
    assert s["missing_cells_after"] == int((df == "").sum().sum())
    assert s["rows_before"] - s["rows_after"] == s["duplicate_rows_removed"] + s["rows_removed_for_missing_values"]
    assert rep["requested_vs_applied"]["requested"]
    assert rep["file"]["output_name"] == "t_cleaned.csv"


def test_xlsx_output_is_typed_and_verified(h):
    data = "name,qty,when,note\nA,\"1,000\",2023-01-05,=SUM(1+1)\nB,20,05/02/2023,x\n"
    j, df, m = run(h, {"output_format": "xlsx"}, data)
    out = pd.read_excel(h.store.job_dir(j["id"]) / "output" / "cleaned.xlsx")
    assert out["qty"].tolist() == [1000, 20]
    assert str(out["when"].iloc[0])[:10] == "2023-01-05"
    assert out["note"].tolist() == ["=SUM(1+1)", "x"]          # kept as text, not evaluated


def test_xlsx_output_with_blank_last_column_and_blank_row_verifies(h):
    j, _, m = run(h, {"output_format": "xlsx", "remove_duplicates": False}, "a,b,c\n1,x,\n,,\n2,y,\n")
    assert j["state"] == "completed" and m["rows_out"] == 3 and m["cols_out"] == 3


def test_single_column_with_empty_values_roundtrips(h):
    j, df, m = run(h, {"remove_duplicates": False}, "only\nx\n\" \"\ny\n")
    assert m["rows_out"] == 3 and df["only"].tolist() == ["x", "", "y"]
    j2, df2, m2 = run(h, {"remove_duplicates": False, "missing": {"strategy": "mode"}}, "only\nx\n\" \"\nx\n")
    assert df2["only"].tolist() == ["x", "x", "x"] and m2["missing_filled"] == 1


def test_xlsx_roundtrip_input_sample(h):
    from pathlib import Path
    data = (Path(__file__).resolve().parent.parent / "samples" / "messy_customers.xlsx").read_bytes()
    j = h.upload("messy_customers.xlsx", data)
    done = h.process(j["id"], {"missing": {"strategy": "fill_text", "text": "n/a"}})
    assert done["state"] == "completed"
    assert done["result"]["metrics"]["rows_out"] == 9


def test_chunk_boundaries_do_not_change_results(h, monkeypatch):
    rows = ["id,grp,val"]
    for i in range(200):
        rows.append(f"{i % 37},{'g' + str(i % 5)},{'' if i % 11 == 0 else (i % 37) * 2}")
    data = "\n".join(rows) + "\n"
    plan = {"missing": {"strategy": "mean", "columns": [2]}}
    _, big, mb = run(h, plan, data)
    monkeypatch.setattr(config, "MIN_CHUNK_ROWS", 7)
    monkeypatch.setattr(config, "MAX_CHUNK_ROWS", 7)
    _, small, ms = run(h, plan, data)
    pd.testing.assert_frame_equal(big, small)
    for k in ("rows_out", "duplicates_removed", "missing_filled", "missing_after", "missing_before"):
        assert mb[k] == ms[k]
    assert mb["duplicates_removed"] > 0


def test_median_chunked_matches_unchunked(h, monkeypatch):
    data = "v\n" + "\n".join(("" if i % 4 == 0 else str((i * 7) % 23)) for i in range(60)) + "\n"
    plan = {"remove_duplicates": False, "missing": {"strategy": "median"}}
    _, a, _ = run(h, plan, data)
    monkeypatch.setattr(config, "MIN_CHUNK_ROWS", 5)
    monkeypatch.setattr(config, "MAX_CHUNK_ROWS", 5)
    _, b, _ = run(h, plan, data)
    pd.testing.assert_frame_equal(a, b)
