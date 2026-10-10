"""Upload handling, format validation and the 500 MB limit."""
import io
import re
from pathlib import Path

import pytest

from datamedic import config, create_app

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


def test_limit_is_exactly_500_mb_everywhere():
    assert config.MAX_FILE_BYTES == 524_288_000
    assert config.MAX_CONTENT_LENGTH == config.MAX_FILE_BYTES + config.MULTIPART_OVERHEAD_BYTES
    js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert re.search(r"DEFAULT_MAX\s*=\s*524288000", js)
    run_py = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "max_request_body_size=config.MAX_CONTENT_LENGTH" in run_py


def test_config_endpoint_reports_limit(client):
    j = client.get("/api/config").json
    assert j["max_file_bytes"] == 524_288_000
    assert ".xlsx" in j["allowed_extensions"]


def test_valid_small_csv(h):
    j = h.upload("ok.csv", "a,b\n1,2\n3,4\n")
    assert j["state"] == "analyzed"
    a = h.analysis(j["id"])
    assert (a["summary"]["rows"], a["summary"]["columns"]) == (2, 2)


def test_valid_xlsx_and_xls_samples(h):
    for name in ("messy_customers.xlsx", "messy_customers.xls"):
        j = h.upload(name, (SAMPLES / name).read_bytes())
        assert j["state"] == "analyzed", (name, j["error"])
        a = h.analysis(j["id"])
        # the CSV twin has 9 columns; the workbook's trailing all-empty column is not part of the used range
        assert a["summary"]["rows"] == 10 and a["summary"]["columns"] == 8
        assert a["file"]["sheet"] == "Customers"
        if name.endswith("xlsx"):   # the second sheet is reported, not silently merged
            assert any("Ignored" in i["detail"] for i in a["issues"])


def test_empty_file_rejected(client):
    r = client.post("/api/jobs", data={"file": (io.BytesIO(b""), "e.csv")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "empty" in r.json["error"]["message"].lower()


def test_unsupported_extension(client):
    r = client.post("/api/jobs", data={"file": (io.BytesIO(b"x"), "evil.exe")}, content_type="multipart/form-data")
    assert r.status_code == 415


def test_no_file(client):
    assert client.post("/api/jobs", data={}, content_type="multipart/form-data").status_code == 400


def test_header_only_csv_fails_clearly(h):
    j = h.upload("h.csv", "a,b,c\n")
    assert j["state"] == "failed" and "no data" in j["error"]["message"].lower()


def test_malformed_csv(h):
    j = h.upload("bad.csv", (SAMPLES / "malformed.csv").read_bytes())
    assert j["state"] == "failed"
    assert "malformed" in j["error"]["message"].lower()
    assert "Traceback" not in j["error"]["message"] and "C:\\" not in j["error"]["message"]


def test_unbalanced_quote_is_reported_not_swallowed(h):
    j = h.upload("q.csv", 'a,b\n1,"unterminated\n2,3\n')
    assert j["state"] == "failed"


def test_cp1252_file_is_decoded_with_warning(h):
    j = h.upload("enc.csv", (SAMPLES / "encoding_cp1252.csv").read_bytes())
    assert j["state"] == "analyzed"
    a = h.analysis(j["id"])
    assert a["file"]["encoding"] == "cp1252"
    assert any("UTF-8" in i["detail"] for i in a["issues"] if i["id"] == "file-warning")
    done = h.process(j["id"], {})
    assert "José" in h.out_df(done["id"])["name"].tolist()


def test_binary_masquerading_as_csv(client):
    r = client.post("/api/jobs", data={"file": (io.BytesIO(b"\x00\x01\x02" * 100), "x.csv")}, content_type="multipart/form-data")
    assert r.status_code == 422


def test_fake_xlsx_and_fake_xls(client):
    for n in ("x.xlsx", "x.xls"):
        r = client.post("/api/jobs", data={"file": (io.BytesIO(b"just text, not a workbook"), n)}, content_type="multipart/form-data")
        assert r.status_code == 422


def test_corrupt_xlsx_zip(h):
    j = h.upload("c.xlsx", b"PK\x03\x04" + b"garbage" * 50)
    assert j["state"] == "failed"


def test_workbook_zip_bomb_guard(h, monkeypatch):
    monkeypatch.setattr(config, "MAX_XLSX_UNCOMPRESSED_BYTES", 1000)
    j = h.upload("m.xlsx", (SAMPLES / "messy_customers.xlsx").read_bytes())
    assert j["state"] == "failed" and "safety limit" in j["error"]["message"]


def test_size_limit_boundary(tmp_path, monkeypatch):
    """Same code path as the real 500 MB limit, with a tiny limit so it runs instantly.
    scripts/limit_check.py performs the real 524,288,000 / 524,288,001 byte test."""
    monkeypatch.setattr(config, "MAX_FILE_BYTES", 2000)
    monkeypatch.setattr(config, "MAX_CONTENT_LENGTH", 2000 + 4096)
    app = create_app(tmp_path / "d")
    c = app.test_client()

    def put(n):
        body = ("a\n" + "x" * (n - 2)).encode()
        assert len(body) == n
        return c.post("/api/jobs", data={"file": (io.BytesIO(body), "f.csv")}, content_type="multipart/form-data")

    assert put(2000).status_code == 201                  # exactly at the limit
    over = put(2001)                                      # one byte over
    assert over.status_code == 413 and "limit" in over.json["error"]["message"]
    huge = put(2000 + 10_000)                             # rejected by the request-size guard
    assert huge.status_code == 413
    # nothing left behind for rejected uploads
    dirs = list((tmp_path / "d" / "jobs").iterdir())
    assert len(dirs) == 1


def test_path_traversal_and_bad_ids(client):
    for bad in ("../../etc/passwd", "..", "ZZZ", "0" * 31, "g" * 32):
        assert client.get(f"/api/jobs/{bad}").status_code in (404, 400)
        assert client.get(f"/api/jobs/{bad}/download/cleaned").status_code in (404, 400)
    assert client.get("/api/jobs/" + "a" * 32).status_code == 404


def test_hostile_filename_is_never_used_on_disk(h):
    r = h.upload("../../..\\evil<script>.csv", "a,b\n1,2\n", wait=False)
    assert r.status_code == 201
    jid = r.json["id"]
    assert "/" not in r.json["original_name"] and "\\" not in r.json["original_name"]
    path = h.store.job_dir(jid) / "source" / "source.csv"
    assert path.exists()
