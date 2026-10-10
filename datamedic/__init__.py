"""DataMedic — Intelligent Data Quality & Repair."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from . import config, ingest
from .errors import DataMedicError
from .jobs import JobStore, sanitize_name

__version__ = "1.0.0"
log = logging.getLogger("datamedic")


def create_app(data_dir: str | Path | None = None, start_cleanup: bool = False) -> Flask:
    root = Path(data_dir).resolve() if data_dir else config.data_dir()
    root.mkdir(parents=True, exist_ok=True)
    _setup_logging(root)

    app = Flask(__name__, static_folder=str(config.ROOT_DIR / "static"), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
    app.config["JSON_SORT_KEYS"] = False
    store = JobStore(root)
    app.extensions["datamedic_store"] = store
    if start_cleanup:
        store.cleanup_expired()
        store.start_cleanup_thread()

    def err(message: str, code: str, status: int):
        return jsonify({"error": {"message": message, "code": code}}), status

    def body() -> dict:
        if request.content_length and request.content_length > 1024 * 1024:
            raise DataMedicError("The request body is too large.", "too_large", 413)
        data = request.get_json(silent=True)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise DataMedicError("The request body must be a JSON object.", "bad_request", 400)
        return data

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "version": __version__})

    @app.get("/api/config")
    def get_config():
        return jsonify({
            "max_file_bytes": config.MAX_FILE_BYTES,
            "allowed_extensions": list(config.ALLOWED_EXTENSIONS),
            "preview_rows": config.PREVIEW_ROWS, "preview_cols": config.PREVIEW_COLS,
            "dry_run_max_bytes": config.DRY_RUN_MAX_BYTES, "xlsx_max_rows": config.XLSX_MAX_ROWS,
            "version": __version__,
        })

    # ------------------------------------------------------------------ jobs
    @app.post("/api/jobs")
    def upload():
        f = request.files.get("file")
        if f is None or not f.filename:
            raise DataMedicError("No file was received. Choose a file and try again.", "no_file", 400)
        name = sanitize_name(f.filename)
        ext = Path(name).suffix.lower()
        if ext not in config.ALLOWED_EXTENSIONS:
            raise DataMedicError(f"Unsupported file type “{ext or 'none'}”. Upload a .csv, .xlsx or .xls file.",
                                 "unsupported_type", 415)
        job = store.create(name, ext)
        dest = store.source_path(job)
        try:
            f.save(dest)
            size = dest.stat().st_size
            ingest.validate_upload(dest, ext)
            with store.lock:
                store._set(job, size=size)
        except BaseException:
            store.discard(job["id"])
            raise
        store.submit_analysis(job["id"])
        return jsonify(store.public(job["id"])), 201

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        return jsonify(store.public(job_id))

    @app.get("/api/jobs/<job_id>/analysis")
    def job_analysis(job_id):
        return jsonify(store.analysis(job_id))

    @app.post("/api/jobs/<job_id>/analyze")
    def reanalyze(job_id):
        store.submit_analysis(job_id)
        return jsonify(store.public(job_id)), 202

    @app.post("/api/jobs/<job_id>/preview")
    def preview(job_id):
        return jsonify(store.preview(job_id, body().get("plan", {})))

    @app.post("/api/jobs/<job_id>/process")
    def process(job_id):
        store.submit_processing(job_id, body().get("plan", {}))
        return jsonify(store.public(job_id)), 202

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        store.request_cancel(job_id)
        return jsonify(store.public(job_id)), 202

    @app.delete("/api/jobs/<job_id>")
    def delete(job_id):
        store.delete(job_id)
        return jsonify({"deleted": True})

    @app.get("/api/jobs/<job_id>/download/<kind>")
    def download(job_id, kind):
        path, name, mime = store.download_path(job_id, kind)
        return send_file(path, mimetype=mime, as_attachment=True, download_name=sanitize_name(name))

    # ------------------------------------------------------------------ errors
    @app.errorhandler(DataMedicError)
    def on_domain_error(e: DataMedicError):
        return err(e.message, e.code, e.status)

    @app.errorhandler(RequestEntityTooLarge)
    def on_too_large(_e):
        return err(f"The upload is larger than the {config.MAX_FILE_BYTES // 1024 ** 2} MB "
                   f"({config.MAX_FILE_BYTES:,} bytes) limit.", "file_too_large", 413)

    @app.errorhandler(HTTPException)
    def on_http(e: HTTPException):
        if request.path.startswith("/api/"):
            return err(e.description or e.name, e.name.lower().replace(" ", "_"), e.code or 500)
        return e

    @app.errorhandler(Exception)
    def on_unexpected(e):
        log.exception("Unhandled error on %s", request.path)
        return err("Something went wrong on the server. Please try again.", "internal", 500)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.path.startswith("/api/") and "download" not in request.path:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


def _setup_logging(root: Path) -> None:
    logger = logging.getLogger("datamedic")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(root / "datamedic.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
