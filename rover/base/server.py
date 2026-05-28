import os
import subprocess
import time
from fastapi import FastAPI, Response

app = FastAPI()

jobs = {
    "map": {"process": None, "status": "idle", "started_at": None, "stderr": None},
    "report": {"process": None, "status": "idle", "started_at": None, "stderr": None},
}

SCRIPTS = {
    "map": "/rover/run_mapping.sh",
    "report": "/rover/run_reporting.sh",
}

OUTPUT_FILES = {
    "map": "/rover/output/map.json",
    "report": "/rover/output/report.md",
}

LOG_FILES = {
    "map": "/rover/output/.map.log",
    "report": "/rover/output/.report.log",
}


def _start_job(name: str):
    job = jobs[name]
    if job["status"] == "running":
        proc = job["process"]
        if proc and proc.poll() is None:
            return None  # already running
    log_file = open(LOG_FILES[name], "w")
    env = {
        **os.environ,
        "GATEWAY_URL": os.environ.get("GATEWAY_URL", "http://gateway:3000"),
    }
    proc = subprocess.Popen(
        ["bash", SCRIPTS[name]],
        cwd="/rover",
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    job["process"] = proc
    job["status"] = "running"
    job["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    job["stderr"] = None
    job["_log_file"] = log_file
    return proc


def _sync_job(name: str):
    job = jobs[name]
    if job["status"] != "running":
        return
    proc = job["process"]
    if proc and proc.poll() is not None:
        job["_log_file"].close()
        if proc.returncode == 0:
            job["status"] = "done"
        else:
            job["status"] = "error"
            try:
                with open(LOG_FILES[name]) as f:
                    job["stderr"] = f.read()
            except Exception:
                job["stderr"] = f"process exited with code {proc.returncode}"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/map")
def start_map():
    result = _start_job("map")
    if result is None:
        return Response(
            content='{"status": "error", "error": "mapping job already running"}',
            status_code=409,
            media_type="application/json",
        )
    return Response(
        content='{"status": "running"}',
        status_code=202,
        media_type="application/json",
    )


@app.get("/get-map")
def get_map():
    _sync_job("map")
    job = jobs["map"]
    if job["status"] == "idle":
        return Response(
            content='{"status": "idle", "error": "no mapping job has been started"}',
            status_code=404,
            media_type="application/json",
        )
    if job["status"] == "running":
        import json

        return Response(
            content=json.dumps({"status": "running", "started_at": job["started_at"]}),
            status_code=202,
            media_type="application/json",
        )
    if job["status"] == "error":
        import json

        return Response(
            content=json.dumps(
                {"status": "error", "error": job["stderr"] or "unknown error"}
            ),
            status_code=500,
            media_type="application/json",
        )
    # done
    try:
        with open(OUTPUT_FILES["map"]) as f:
            content = f.read()
        return Response(content=content, status_code=200, media_type="application/json")
    except FileNotFoundError:
        return Response(
            content='{"status": "error", "error": "map.json not found after job completed"}',
            status_code=500,
            media_type="application/json",
        )


@app.post("/report")
def start_report():
    result = _start_job("report")
    if result is None:
        return Response(
            content='{"status": "error", "error": "reporting job already running"}',
            status_code=409,
            media_type="application/json",
        )
    return Response(
        content='{"status": "running"}',
        status_code=202,
        media_type="application/json",
    )


@app.get("/get-report")
def get_report():
    _sync_job("report")
    job = jobs["report"]
    if job["status"] == "idle":
        return Response(
            content='{"status": "idle", "error": "no reporting job has been started"}',
            status_code=404,
            media_type="application/json",
        )
    if job["status"] == "running":
        import json

        return Response(
            content=json.dumps({"status": "running", "started_at": job["started_at"]}),
            status_code=202,
            media_type="application/json",
        )
    if job["status"] == "error":
        import json

        return Response(
            content=json.dumps(
                {"status": "error", "error": job["stderr"] or "unknown error"}
            ),
            status_code=500,
            media_type="application/json",
        )
    # done
    try:
        with open(OUTPUT_FILES["report"]) as f:
            content = f.read()
        return Response(content=content, status_code=200, media_type="text/markdown")
    except FileNotFoundError:
        return Response(
            content='{"status": "error", "error": "report.md not found after job completed"}',
            status_code=500,
            media_type="application/json",
        )
