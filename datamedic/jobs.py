"""Job store: on-disk job folders + an explicit state machine.

  uploaded -> analyzing -> analyzed -> processing -> completed
                  \\            \\           \\-> failed (failed_stage = processing)
                   \\-> failed (failed_stage = analysis)

completed / failed(processing) / analyzed may be re-submitted for processing.
Downloads are only offered while state == completed AND the files exist.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import analyzer, config, ingest, plan as planmod, report as reportmod
from .errors import DataMedicError, JobCancelled, JobTimeout
from .pipeline import Pipeline, STAGES
from .writers import verify_output

log = logging.getLogger("datamedic.jobs")
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUNNING = ("analyzing", "processing")


def sanitize_name(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", name).strip(" .")
    return name[:150] or "upload"


class JobStore:
    def __init__(self, root: Path, workers: int = config.MAX_WORKERS):
        self.root = Path(root)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.cancel: set[str] = set()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="datamedic")
        self.preview_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_PREVIEWS)
        self._load_existing()

    # ---- persistence ------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise DataMedicError("Job not found.", "not_found", 404)
        return self.root / "jobs" / job_id

    def _save(self, job: dict) -> None:
        d = self.job_dir(job["id"])
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job), encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def _load_existing(self) -> None:
        for d in (self.root / "jobs").iterdir():
            f = d / "job.json"
            if not (d.is_dir() and JOB_ID_RE.fullmatch(d.name) and f.exists()):
                continue
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("state") in RUNNING:
                job["failed_stage"] = "analysis" if job["state"] == "analyzing" else "processing"
                job["state"] = "failed"
                job["error"] = {"message": "The server restarted while this job was running. Please retry.", "code": "interrupted"}
                self._save(job)
            self.jobs[job["id"]] = job

    # ---- access -----------------------------------------------------------
    def _get(self, job_id: str) -> dict:
        self.job_dir(job_id)
        job = self.jobs.get(job_id)
        if job is None:
            raise DataMedicError("Job not found (it may have expired).", "not_found", 404)
        return job

    def public(self, job_id: str) -> dict:
        with self.lock:
            j = self._get(job_id)
            d = self.job_dir(job_id)
            now = time.time()
            t = j.get("timing") or {}
            elapsed = None
            if t.get("started"):
                elapsed = round((t.get("finished") or now) - t["started"], 1)
            downloads = {}
            if j["state"] == "completed":
                out = j.get("output") or {}
                for key, rel in (("cleaned", out.get("path")), ("report", "output/report.html"),
                                 ("report_json", "output/report.json")):
                    p = d / rel if rel else None
                    downloads[key] = bool(p and p.exists() and p.stat().st_size > 0)
            return {
                "id": j["id"], "state": j["state"], "failed_stage": j.get("failed_stage"),
                "original_name": j["original_name"], "size": j["size"], "kind": j["kind"],
                "created_at": j["created_at"], "progress": j.get("progress"),
                "elapsed_seconds": elapsed, "error": j.get("error"),
                "analysis_ready": (d / "analysis.json").exists() and not (
                    j["state"] in ("uploaded", "analyzing")
                    or (j["state"] == "failed" and j.get("failed_stage") == "analysis")),
                "summary": j.get("summary"), "plan": j.get("plan"), "raw_plan": j.get("raw_plan"),
                "result": j.get("result"), "output": {k: v for k, v in (j.get("output") or {}).items() if k != "path"} or None,
                "downloads": downloads,
            }

    def analysis(self, job_id: str) -> dict:
        with self.lock:
            j = self._get(job_id)
            if j["state"] in ("uploaded", "analyzing") or (j["state"] == "failed" and j.get("failed_stage") == "analysis"):
                raise DataMedicError("The analysis is not ready yet.", "not_ready", 409)
        return json.loads((self.job_dir(job_id) / "analysis.json").read_text(encoding="utf-8"))

    def _set(self, job: dict, **fields) -> None:
        job.update(fields)
        job["updated_at"] = time.time()
        self._save(job)

    # ---- creation ---------------------------------------------------------
    def create(self, original_name: str, ext: str, size_hint: int | None = None) -> dict:
        job_id = uuid.uuid4().hex
        d = self.job_dir(job_id)
        (d / "source").mkdir(parents=True)
        job = {"id": job_id, "state": "uploaded", "original_name": sanitize_name(original_name),
               "kind": ext.lstrip("."), "size": 0, "created_at": time.time(), "updated_at": time.time(),
               "source_rel": f"source/source{ext}"}
        with self.lock:
            self.jobs[job_id] = job
        return job

    def source_path(self, job: dict) -> Path:
        return self.job_dir(job["id"]) / job["source_rel"]

    def discard(self, job_id: str) -> None:
        with self.lock:
            self.jobs.pop(job_id, None)
            self.cancel.discard(job_id)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def delete(self, job_id: str) -> None:
        with self.lock:
            j = self._get(job_id)
            if j["state"] in RUNNING:
                raise DataMedicError("This job is still running. Cancel it first.", "busy", 409)
        self.discard(job_id)

    # ---- analysis ---------------------------------------------------------
    def submit_analysis(self, job_id: str) -> None:
        with self.lock:
            j = self._get(job_id)
            if not (j["state"] == "uploaded" or (j["state"] == "failed" and j.get("failed_stage") == "analysis")):
                raise DataMedicError("This job cannot be analysed in its current state.", "bad_state", 409)
            self.cancel.discard(job_id)
            self._set(j, state="analyzing", error=None, failed_stage=None,
                      timing={"started": time.time(), "finished": None},
                      progress={"stage": "read", "label": "Reading file", "done": None, "total": None,
                                "stages": [{"key": "read", "label": "Validating and reading file"},
                                           {"key": "scan", "label": "Scanning every row for issues"}]})
        self.executor.submit(self._run_analysis, job_id)

    def _progress(self, job_id: str, stage: str, label: str, done, total) -> None:
        with self.lock:
            j = self.jobs.get(job_id)
            if j and j.get("progress") is not None:
                pr = j["progress"]
                pr.update(stage=stage, label=label, done=done, total=total)

    def _make_check(self, job_id: str, started: float):
        def check():
            if job_id in self.cancel:
                raise JobCancelled()
            if time.monotonic() - started > config.JOB_TIMEOUT_S:
                raise JobTimeout(config.JOB_TIMEOUT_S)
        return check

    def _run_analysis(self, job_id: str) -> None:
        d = self.job_dir(job_id)
        try:
            with self.lock:
                j = self._get(job_id)
            started = time.monotonic()
            check = self._make_check(job_id, started)
            src = ingest.prepare_source(d, j["source_rel"], j["kind"])
            self._progress(job_id, "scan", "Scanning every row for issues", 0, None)
            file_meta = {"name": j["original_name"], "size": j["size"], "kind": j["kind"],
                         "encoding": src.encoding if j["kind"] == "csv" else "n/a (workbook)",
                         "delimiter": src.delimiter if j["kind"] == "csv" else None,
                         "sheet": src.sheet, "sheets": src.sheets}
            result = analyzer.analyze(d, src, file_meta,
                                      progress=lambda n: self._progress(job_id, "scan", "Scanning every row for issues", n, None),
                                      check=check)
            result["defaults"] = planmod.DEFAULT_PLAN
            (d / "analysis.json").write_text(json.dumps(result), encoding="utf-8")
            with self.lock:
                j = self._get(job_id)
                t = dict(j["timing"], finished=time.time())
                self._set(j, state="analyzed", source=src.to_dict(), timing=t, progress=None,
                          summary={**result["summary"], "issue_counts": result["issue_counts"]})
        except JobCancelled:
            self._fail(job_id, "analysis", "Analysis was cancelled.", "cancelled")
        except DataMedicError as e:
            self._fail(job_id, "analysis", e.message, e.code)
        except MemoryError:
            self._fail(job_id, "analysis", "The server ran out of memory while analysing this file.", "memory")
        except Exception:
            log.exception("analysis failed for %s", job_id)
            self._fail(job_id, "analysis", f"Unexpected error while analysing the file (reference {job_id[:8]}).", "internal")

    def _fail(self, job_id: str, stage: str, message: str, code: str) -> None:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j:
                return
            t = dict(j.get("timing") or {}, finished=time.time())
            self._set(j, state="failed", failed_stage=stage, error={"message": message, "code": code},
                      progress=None, timing=t)
        shutil.rmtree(self.job_dir(job_id) / "work", ignore_errors=True)

    # ---- processing -------------------------------------------------------
    def _load_ctx(self, job_id: str):
        d = self.job_dir(job_id)
        analysis = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
        with self.lock:
            j = self._get(job_id)
            src = ingest.SourceInfo(**j["source"])
        return d, analysis, src

    def submit_processing(self, job_id: str, raw_plan: dict) -> None:
        with self.lock:
            j = self._get(job_id)
            ok = j["state"] in ("analyzed", "completed") or (j["state"] == "failed" and j.get("failed_stage") == "processing")
            if not ok:
                raise DataMedicError("This job is not ready to be processed (or is already running).", "bad_state", 409)
            d, analysis, src = self._load_ctx(job_id)
            resolved = planmod.resolve_plan(raw_plan, analysis)   # validate BEFORE touching old outputs
            shutil.rmtree(d / "output", ignore_errors=True)       # old result is invalidated now
            (d / "output").mkdir()
            self.cancel.discard(job_id)
            stages = [{"key": k, "label": lbl} for k, lbl in STAGES
                      if k != "fill" or resolved["missing_strategy"] in ("fill_zero", "mean", "median", "mode")]
            self._set(j, state="processing", error=None, failed_stage=None, result=None, output=None,
                      plan=resolved, raw_plan=raw_plan, timing={"started": time.time(), "finished": None},
                      progress={"stage": "prepare", "label": "Preparing data", "done": None, "total": None, "stages": stages})
        self.executor.submit(self._run_processing, job_id, resolved)

    def _run_processing(self, job_id: str, plan: dict) -> None:
        d = self.job_dir(job_id)
        started_wall = time.time()
        try:
            d, analysis, src = self._load_ctx(job_id)
            check = self._make_check(job_id, time.monotonic())
            fmt = plan["output_format"]
            partial = d / "output" / f"cleaned_partial.{fmt}"
            final = d / "output" / f"cleaned.{fmt}"
            labels = dict(STAGES)

            def progress(stage, done, total):
                self._progress(job_id, stage, labels[stage], done, total)

            pipe = Pipeline(d, src, analysis, plan, out_path=partial, work_dir=d / "work" / "run",
                            progress=progress, check=check)
            result = pipe.run()
            m = result["metrics"]
            self._progress(job_id, "validate", labels["validate"], None, None)
            check()
            verify_output(partial, fmt, m["rows_out"], m["cols_out"])
            os.replace(partial, final)
            self._progress(job_id, "report", labels["report"], None, None)
            with self.lock:
                j = self._get(job_id)
                original = dict(j)
            stem = Path(original["original_name"]).stem or "data"
            out_name = f"{stem}_cleaned.{fmt}"
            rep = reportmod.build_report(original, analysis, plan, result, out_name, final.stat().st_size,
                                         started_wall, time.time())
            reportmod.write_reports(d / "output", rep)
            with self.lock:
                j = self._get(job_id)
                t = dict(j["timing"], finished=time.time())
                self._set(j, state="completed", progress=None, timing=t,
                          output={"path": f"output/cleaned.{fmt}", "name": out_name, "format": fmt,
                                  "size": final.stat().st_size,
                                  "report_name": f"{stem}_report.html", "report_json_name": f"{stem}_report.json"},
                          result={"metrics": m, "operations": result["operations"], "warnings": result["warnings"],
                                  "columns": result["columns"], "email_samples": result["email_samples"]})
        except JobCancelled:
            shutil.rmtree(d / "output", ignore_errors=True)
            with self.lock:
                j = self.jobs.get(job_id)
                if j:
                    self._set(j, state="analyzed", progress=None, error=None, timing=dict(j["timing"], finished=time.time()))
        except DataMedicError as e:
            shutil.rmtree(d / "output", ignore_errors=True)
            self._fail(job_id, "processing", e.message, e.code)
        except MemoryError:
            shutil.rmtree(d / "output", ignore_errors=True)
            self._fail(job_id, "processing", "The server ran out of memory while processing this file. "
                       "Try fewer treatments (e.g. avoid median) or a smaller file.", "memory")
        except Exception:
            log.exception("processing failed for %s", job_id)
            shutil.rmtree(d / "output", ignore_errors=True)
            self._fail(job_id, "processing", f"Unexpected error while processing the file (reference {job_id[:8]}).", "internal")

    def request_cancel(self, job_id: str) -> None:
        with self.lock:
            j = self._get(job_id)
            if j["state"] not in RUNNING:
                raise DataMedicError("Nothing is running for this job.", "bad_state", 409)
            self.cancel.add(job_id)

    # ---- preview ----------------------------------------------------------
    def preview(self, job_id: str, raw_plan: dict) -> dict:
        with self.lock:
            j = self._get(job_id)
            if j["state"] in ("uploaded", "analyzing") or (j["state"] == "failed" and j.get("failed_stage") == "analysis"):
                raise DataMedicError("The analysis is not ready yet.", "not_ready", 409)
            if j["state"] == "processing":
                raise DataMedicError("The job is being processed; preview is unavailable.", "busy", 409)
            d, analysis, src = self._load_ctx(job_id)
            plan = planmod.resolve_plan(dict(raw_plan or {}, output_format="csv"), analysis)
            read_size = (d / src.read_path).stat().st_size
        if not self.preview_slots.acquire(blocking=False):
            raise DataMedicError("The server is busy generating other previews. Try again in a moment.", "busy", 429)
        work = d / "work" / f"preview-{uuid.uuid4().hex[:8]}"
        try:
            exact = read_size <= config.DRY_RUN_MAX_BYTES
            started = time.monotonic()
            pipe = Pipeline(d, src, analysis, plan, out_path=None, work_dir=work,
                            check=self._make_check("-", started), preview_rows=config.PREVIEW_ROWS,
                            limit_rows=None if exact else config.SAMPLE_PREVIEW_ROWS)
            result = pipe.run()
        finally:
            self.preview_slots.release()
            shutil.rmtree(work, ignore_errors=True)
        return {"exact": exact, "sample_rows": None if exact else result["metrics"]["rows_in"],
                "total_rows": analysis["summary"]["rows"], "metrics": result["metrics"],
                "operations": result["operations"], "warnings": result["warnings"],
                "preview": result["preview"], "plan": plan}

    # ---- downloads --------------------------------------------------------
    def download_path(self, job_id: str, kind: str) -> tuple[Path, str, str]:
        with self.lock:
            j = self._get(job_id)
            if j["state"] != "completed":
                raise DataMedicError("The result is not ready yet.", "not_ready", 409)
            out = j.get("output") or {}
        d = self.job_dir(job_id)
        table = {
            "cleaned": (d / out.get("path", ""), out.get("name", "cleaned"),
                        "text/csv; charset=utf-8" if out.get("format") == "csv"
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "report": (d / "output" / "report.html", out.get("report_name", "report.html"), "text/html; charset=utf-8"),
            "report-json": (d / "output" / "report.json", out.get("report_json_name", "report.json"), "application/json"),
        }
        if kind not in table:
            raise DataMedicError("Unknown download.", "not_found", 404)
        path, name, mime = table[kind]
        if not path.is_file() or path.stat().st_size == 0:
            raise DataMedicError("The file is not available.", "not_ready", 409)
        return path, name, mime

    # ---- cleanup ----------------------------------------------------------
    def cleanup_expired(self) -> int:
        cutoff = time.time() - config.JOB_TTL_HOURS * 3600
        removed = 0
        with self.lock:
            stale = [i for i, j in self.jobs.items() if j["state"] not in RUNNING and j["updated_at"] < cutoff]
        for i in stale:
            self.discard(i)
            removed += 1
        for d in (self.root / "jobs").iterdir():          # orphan folders
            if d.is_dir() and d.name not in self.jobs and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        return removed

    def start_cleanup_thread(self) -> None:
        def loop():
            while True:
                time.sleep(config.CLEANUP_INTERVAL_S)
                try:
                    self.cleanup_expired()
                except Exception:
                    log.exception("cleanup failed")
        threading.Thread(target=loop, daemon=True, name="datamedic-cleanup").start()
