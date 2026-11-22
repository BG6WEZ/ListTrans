"""Drive real upload/cancel/status HTTP routes on a native loopback server."""
import argparse
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time

from m3_r01_quality import EVIDENCE, GOVERNANCE, GOLDEN, SWITCHES, V017
from m0_r01_benchmark import block_external_network, configure_actor, make_workbook, stage
from m0_r01_build_governance import read_json, save_json


def run(output):
    if output.exists():
        raise FileExistsError(output)
    os.environ.update(SWITCHES)
    labels = read_json(GOVERNANCE)["samples"]
    source = [item for item in read_json(GOLDEN)["items"] if labels[item["id"]]["split"] == "train"][:150]
    with TemporaryDirectory(prefix="v019_m3_cancel_", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve() / "service"
        stage(root, "keyword", read_json(V017 / "m0_r01_before.json"))
        sys.addaudithook(block_external_network)
        import httpx
        import uvicorn
        from app import server, runtime_retention
        from app.runtime_jobs import current_job, upload_jobs

        worker = server.async_process_upload_task

        def run_worker(task_id, temp_path, **kwargs):
            if temp_path.endswith("_probe-deadline.xlsx"):
                # Use a short clock budget for this test job. The real worker
                # still executes its own checkpoints and terminal cleanup.
                current_job().deadline = time.monotonic() + 0.035
            return worker(task_id, temp_path, **kwargs)

        server.async_process_upload_task = run_worker
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound:
            bound.bind(("127.0.0.1", 0))
            port = bound.getsockname()[1]
            app = uvicorn.Server(uvicorn.Config(server.app, lifespan="on", log_level="warning"))
            thread = threading.Thread(target=app.run, kwargs={"sockets": [bound]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 45
                while not app.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.025)
                if not app.started:
                    raise RuntimeError("Isolated Uvicorn did not start")
                headers = configure_actor()
                reports = {}
                workbook = make_workbook(source)
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=45, trust_env=False) as client:
                    for target, phrase in (("cancel", "任务已取消"), ("deadline", "任务处理超时")):
                        start = time.perf_counter()
                        response = client.post("/api/upload", files={
                            "file": (f"probe-{target}.xlsx", workbook,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                        })
                        response.raise_for_status()
                        task = response.json()["task_id"]
                        cancel = client.post(f"/api/upload/cancel/{task}") if target == "cancel" else None
                        if cancel is not None:
                            assert cancel.status_code == 200 and cancel.json()["cancel_requested"]
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            status = client.get("/api/upload/status", params={"task_id": task})
                            status.raise_for_status()
                            data = status.json()
                            if data["status"] != "processing" and upload_jobs.stats()["admitted"] == 0:
                                break
                            time.sleep(.015)
                        pools = upload_jobs.stats()
                        files = list(Path(os.environ["UPLOAD_DIR"]).iterdir())
                        leased = [p for p in runtime_retention._active if Path(os.environ["UPLOAD_DIR"]) in p.parents]
                        assert data["status"] in {"failed", "cancelled", "timed_out"} and phrase in (data.get("error") or "")
                        assert data["result"] is None and pools["admitted"] == 0 and not files and not leased
                        reports[target] = {
                            "admitted_http": response.status_code, "cancel_http": cancel.status_code if cancel else None,
                            "terminal_status": data["status"], "terminal_reason": data["error"],
                            "reclaimed_after_ms": round((time.perf_counter() - start) * 1000, 3),
                            "admitted_after": pools["admitted"], "upload_files_after": len(files),
                            "upload_file_leases_after": len(leased), "result_written": False,
                        }
                    healthy = client.post("/api/upload", files={"file": ("followup.xlsx", make_workbook(source[:1]))})
                    healthy.raise_for_status()
                    followup = healthy.json()["task_id"]
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        restored = client.get("/api/upload/status", params={"task_id": followup}).json()
                        if restored["status"] != "processing" and upload_jobs.stats()["admitted"] == 0:
                            break
                        time.sleep(.02)
                    assert restored["status"] == "completed" and len(restored["result"]) == 1
                    assert upload_jobs.stats()["admitted"] == 0
                save_json(output, {"rows_per_interrupted_workbook": len(source), "http_transport": "native loopback",
                                   "deadline_probe_seconds_after_worker_start": 0.035,
                                   "probes": reports, "followup_upload": "completed", "exit_code": 0})
            finally:
                app.should_exit = True
                thread.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
