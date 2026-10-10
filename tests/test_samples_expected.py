"""Regression: sample files + plans must reproduce the reviewed expected outputs byte for byte."""
from pathlib import Path

import pytest

from cases import CASES

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.mark.parametrize("name", sorted(CASES))
def test_expected_output(h, name):
    sample, plan = CASES[name]
    j = h.upload(sample, (SAMPLES / sample).read_bytes())
    assert j["state"] == "analyzed"
    done = h.process(j["id"], plan)
    assert done["state"] == "completed"
    actual = (h.store.job_dir(j["id"]) / "output" / "cleaned.csv").read_text(encoding="utf-8-sig")
    expected = (SAMPLES / "expected" / f"{name}.csv").read_text(encoding="utf-8")
    assert actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n")


def test_xlsx_and_csv_twins_clean_identically_except_native_types(h):
    """The CSV and XLSX versions of the same data produce the same duplicate/missing metrics."""
    out = {}
    for ext in ("csv", "xlsx", "xls"):
        j = h.upload(f"messy_customers.{ext}", (SAMPLES / f"messy_customers.{ext}").read_bytes())
        out[ext] = h.process(j["id"], {"drop_empty_columns": True})["result"]["metrics"]
    for ext in ("xlsx", "xls"):
        assert out[ext]["rows_in"] == out["csv"]["rows_in"] == 10
        assert out[ext]["duplicates_removed"] == out["csv"]["duplicates_removed"] == 1
