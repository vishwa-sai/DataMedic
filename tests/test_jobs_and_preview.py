"""Job lifecycle, preview truthfulness, downloads, isolation and failure handling."""
import json
import threading
import time

import pandas as pd
import pytest

from datamedic import config, jobs

DATA = "id,name,city,score\n1, Ann ,Paris,10\n2,Bob,,20\n2,Bob,,20\n3,Cy,Rome,\n4,Di,Rome,40\n"


def test_downloads_blocked_until_ready(h):
    j = h.upload("t.csv", DATA)
    for kind in ("cleaned", "report", "report-json"):
        r = h.c.get(f"/api/jobs/{j['id']}/download/{kind}")
        assert r.status_code == 409
    assert h.c.get(f"/api/jobs/{j['id']}/download/nope").status_code in (404, 409)


def test_downloads_after_completion(h):
    j = h.upload("t.csv", DATA)
    done = h.process(j["id"], {})
    assert done["downloads"] == {"cleaned": True, "report": True, "report_json": True}
    r = h.c.get(f"/api/jobs/{j['id']}/download/cleaned")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert r.headers["Content-Disposition"].count("t_cleaned.csv") == 1
    body = r.data.decode("utf-8-sig")
    assert body.splitlines()[0] == "id,name,city,score"
    rep = h.c.get(f"/api/jobs/{j['id']}/download/report")
    assert rep.status_code == 200 and b"DataMedic quality report" in rep.data
    js = h.c.get(f"/api/jobs/{j['id']}/download/report-json").json
    assert js["summary"]["rows_after"] == 4


def test_preview_matches_final_exactly(h):
    j = h.upload("t.csv", DATA)
    plan = {"missing": {"strategy": "fill_text", "text": "?"}}
    pv = h.c.post(f"/api/jobs/{j['id']}/preview", json={"plan": plan}).json
    assert pv["exact"] is True
    done = h.process(j["id"], plan)
    fm = done["result"]["metrics"]
    for k in ("rows_out", "duplicates_removed", "missing_filled", "missing_after", "cells_trimmed", "cols_out"):
        assert pv["metrics"][k] == fm[k], k
    out = h.out_df(j["id"])
    kept = [r for r in pv["preview"]["rows"] if r["status"] == "kept"]
    assert [r["after"] for r in kept] == out.values.tolist()
    statuses = [r["status"] for r in pv["preview"]["rows"]]
    assert statuses == ["kept", "kept", "duplicate", "kept", "kept"]
    # changed flags tell the truth
    assert pv["preview"]["rows"][0]["changed"][1] is True       # " Ann " -> "Ann"
    assert pv["preview"]["rows"][0]["changed"][0] is False


def test_preview_flags_missing_row_drops_and_does_not_change_job(h):
    j = h.upload("t.csv", DATA)
    before = h.c.get(f"/api/jobs/{j['id']}").json
    pv = h.c.post(f"/api/jobs/{j['id']}/preview", json={"plan": {"missing": {"strategy": "drop_rows"}}}).json
    assert [r["status"] for r in pv["preview"]["rows"]] == ["kept", "dropped_missing", "duplicate", "dropped_missing", "kept"]
    assert pv["metrics"]["rows_out"] == 2
    after = h.c.get(f"/api/jobs/{j['id']}").json
    assert after["state"] == before["state"] == "analyzed" and after["output"] is None


def test_preview_with_statistics_strategy_is_exact(h):
    j = h.upload("t.csv", DATA)
    plan = {"missing": {"strategy": "mean", "columns": [3]}}
    pv = h.c.post(f"/api/jobs/{j['id']}/preview", json={"plan": plan}).json
    row = next(r for r in pv["preview"]["rows"] if r["row"] == 4)
    assert row["after"][3] == "23.333333"
    done = h.process(j["id"], plan)
    assert "23.333333" in h.out_df(j["id"])["score"].tolist()


def test_large_file_preview_is_labelled_as_sample(h, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN_MAX_BYTES", 10)
    monkeypatch.setattr(config, "SAMPLE_PREVIEW_ROWS", 3)
    j = h.upload("t.csv", DATA)
    pv = h.c.post(f"/api/jobs/{j['id']}/preview", json={"plan": {}}).json
    assert pv["exact"] is False and pv["sample_rows"] == 3 and pv["total_rows"] == 5


def test_invalid_plans_rejected(h):
    j = h.upload("t.csv", DATA)
    for plan in ({"trim": "yes"}, {"missing": {"strategy": "explode"}}, {"output_format": "pdf"},
                 {"missing": {"columns": [99]}}, {"dates": {"order": "xyz"}}, "nope"):
        r = h.c.post(f"/api/jobs/{j['id']}/process", json={"plan": plan})
        assert r.status_code == 400, plan
    assert h.c.get(f"/api/jobs/{j['id']}").json["state"] == "analyzed"


def test_state_machine_blocks_invalid_transitions(h):
    j = h.upload("t.csv", DATA)
    assert h.c.post(f"/api/jobs/{j['id']}/analyze").status_code == 409     # already analysed
    done = h.process(j["id"], {})
    assert done["state"] == "completed"
    assert h.c.post(f"/api/jobs/{j['id']}/cancel").status_code == 409


def test_reprocessing_replaces_old_result(h):
    j = h.upload("t.csv", DATA)
    h.process(j["id"], {})
    first = h.out_df(j["id"])
    done = h.process(j["id"], {"remove_duplicates": False})
    second = h.out_df(j["id"])
    assert len(first) == 4 and len(second) == 5
    assert done["result"]["metrics"]["duplicates_removed"] == 0


def test_simultaneous_jobs_are_isolated(h):
    texts = {i: "k,v\n" + "\n".join(f"{i}-{n},{i}" for n in range(30 + i)) + "\n" for i in range(5)}
    results = {}

    def work(i):
        j = h.upload(f"f{i}.csv", texts[i])
        results[i] = h.process(j["id"], {"missing": {"strategy": "preserve"}})

    ts = [threading.Thread(target=work, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = {r["id"] for r in results.values()}
    assert len(ids) == 5
    for i, r in results.items():
        df = h.out_df(r["id"])
        assert len(df) == 30 + i and set(df["v"]) == {str(i)}
        assert r["original_name"] == f"f{i}.csv"


def test_failure_then_retry(h, monkeypatch):
    j = h.upload("t.csv", DATA)
    import datamedic.jobs as jm
    real = jm.Pipeline.run
    calls = {"n": 0}

    def boom(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("C:\\secret\\path exploded")
        return real(self)

    monkeypatch.setattr(jm.Pipeline, "run", boom)
    failed = h.process(j["id"], {})
    assert failed["state"] == "failed" and failed["failed_stage"] == "processing"
    assert "secret" not in json.dumps(failed) and "Traceback" not in json.dumps(failed)
    assert failed["downloads"] == {}
    assert h.c.get(f"/api/jobs/{j['id']}/download/cleaned").status_code == 409
    ok = h.process(j["id"], {})
    assert ok["state"] == "completed" and ok["error"] is None


def test_failed_analysis_can_be_retried(h, monkeypatch):
    import datamedic.jobs as jm
    real = jm.analyzer.analyze
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(*a, **k)

    monkeypatch.setattr(jm.analyzer, "analyze", flaky)
    j = h.upload("t.csv", DATA)
    assert j["state"] == "failed" and j["failed_stage"] == "analysis"
    assert h.c.post(f"/api/jobs/{j['id']}/preview", json={}).status_code == 409
    r = h.c.post(f"/api/jobs/{j['id']}/analyze")
    assert r.status_code == 202
    assert h.wait(j["id"], ("analyzing",))["state"] == "analyzed"


def test_cancel_processing(h, monkeypatch):
    import datamedic.jobs as jm
    gate = threading.Event()
    real_read = jm.Pipeline._stage1

    def slow(self, chunk, start):
        gate.wait(5)
        return real_read(self, chunk, start)

    monkeypatch.setattr(jm.Pipeline, "_stage1", slow)
    j = h.upload("t.csv", DATA)
    assert h.c.post(f"/api/jobs/{j['id']}/process", json={"plan": {}}).status_code == 202
    assert h.c.post(f"/api/jobs/{j['id']}/cancel").status_code == 202
    gate.set()
    back = h.wait(j["id"], ("processing",))
    assert back["state"] == "analyzed" and back["downloads"] == {}
    assert not (h.store.job_dir(j["id"]) / "output").exists()


def test_delete_job_removes_files(h):
    j = h.upload("t.csv", DATA)
    d = h.store.job_dir(j["id"])
    assert d.exists()
    assert h.c.delete(f"/api/jobs/{j['id']}").status_code == 200
    assert not d.exists() and h.c.get(f"/api/jobs/{j['id']}").status_code == 404


def test_original_is_never_modified(h):
    j = h.upload("t.csv", DATA)
    src = h.store.job_dir(j["id"]) / "source" / "source.csv"
    before = src.read_bytes()
    h.process(j["id"], {"missing": {"strategy": "drop_rows"}})
    assert src.read_bytes() == before == DATA.encode()


def test_expired_jobs_are_cleaned_up(h, monkeypatch):
    j = h.upload("t.csv", DATA)
    h.store.jobs[j["id"]]["updated_at"] = time.time() - 48 * 3600
    assert h.store.cleanup_expired() == 1
    assert not h.store.job_dir(j["id"]).exists()


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    from datamedic import create_app
    app = create_app(tmp_path / "x")
    store = app.extensions["datamedic_store"]
    job = store.create("a.csv", ".csv")
    with store.lock:
        store._set(job, state="processing", size=1)
    app2 = create_app(tmp_path / "x")
    j = app2.test_client().get(f"/api/jobs/{job['id']}").json
    assert j["state"] == "failed" and "restarted" in j["error"]["message"]


def test_html_report_escapes_untrusted_values(h):
    j = h.upload("<b>x</b>.csv", '<img src=x onerror=alert(1)>,b\n"<script>alert(1)</script>",bad@@\n')
    h.process(j["id"], {})
    html = h.c.get(f"/api/jobs/{j['id']}/download/report").data.decode()
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html or "&lt;img" in html


def test_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]


def test_errors_never_leak_internals(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and "error" in r.json
