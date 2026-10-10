import io
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datamedic import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app(tmp_path / "data")


@pytest.fixture()
def client(app):
    return app.test_client()


class Helper:
    def __init__(self, app, client):
        self.app, self.c = app, client
        self.store = app.extensions["datamedic_store"]

    def upload(self, name, data, wait=True):
        if isinstance(data, str):
            data = data.encode("utf-8")
        r = self.c.post("/api/jobs", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")
        if r.status_code != 201 or not wait:
            return r
        return self.wait(r.json["id"], ("analyzing", "uploaded"))

    def wait(self, jid, busy, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.c.get(f"/api/jobs/{jid}").json
            if j["state"] not in busy:
                return j
            time.sleep(0.05)
        raise AssertionError("timed out waiting for job")

    def analysis(self, jid):
        return self.c.get(f"/api/jobs/{jid}/analysis").json

    def process(self, jid, plan=None):
        r = self.c.post(f"/api/jobs/{jid}/process", json={"plan": plan or {}})
        assert r.status_code == 202, r.json
        return self.wait(jid, ("processing",))

    def out_df(self, jid, fmt="csv"):
        path = self.store.job_dir(jid) / "output" / f"cleaned.{fmt}"
        if fmt == "csv":
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        return pd.read_excel(path, dtype=object)

    def run(self, name, data, plan=None):
        j = self.upload(name, data)
        assert j["state"] == "analyzed", j
        done = self.process(j["id"], plan)
        return done


@pytest.fixture()
def h(app, client):
    return Helper(app, client)
