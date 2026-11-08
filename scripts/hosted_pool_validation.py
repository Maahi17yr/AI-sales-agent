"""Dedicated disposable hosted PostgreSQL pool validation runner.

No FastAPI routes, no Bedrock/Paystack, no customer-facing API. Runs finite
fictional workload at startup and exposes only a health/result endpoint.
Never use on an existing service or shared database.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from data.database import pool_experiment

def run():
    from data.database import models
    from scripts.benchmark_pool_sizing import percentile
    import time
    from concurrent.futures import ThreadPoolExecutor
    pool_experiment.guard()
    if os.environ.get("NAIJA_BITES_HOSTED_POOL_VALIDATION") != "1":
        raise RuntimeError("Hosted test must be explicitly enabled")
    # Dedicated empty database: no app schema initialization or external calls.
    with models.get_connection() as c:
        version = c.execute("SHOW server_version_num").fetchone()[0]
        if int(version) // 10000 != 18:
            raise RuntimeError("Expected isolated PostgreSQL 18")
        c.execute("CREATE TABLE IF NOT EXISTS pool_hosted_validation (id INT PRIMARY KEY, val INT NOT NULL)")
        c.execute("INSERT INTO pool_hosted_validation (id,val) VALUES (1,0) ON CONFLICT (id) DO NOTHING")
    results = []
    # Repeated same-host comparisons, bounded rate, and finite duration.
    for repeat in range(3):
        for label, mn, mx in (("fresh",0,0),("pool_2_5",2,5),("pool_1_3",1,3)):
            if mn:
                pool_experiment.open_pool(mn,mx,timeout=1.0)
                os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"]="1"
            try:
                for _ in range(5):
                    with models.get_connection() as c:
                        c.execute("SELECT 1")
                latencies=[]
                failures=0
                started=time.perf_counter()
                def work(_):
                    t=time.perf_counter()
                    with models.get_connection() as c:
                        row=c.execute("SELECT val FROM pool_hosted_validation WHERE id=1").fetchone()
                        if row["val"] != 0:
                            raise AssertionError("Unexpected fictional fixture value")
                    return (time.perf_counter()-t)*1000
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futures=[ex.submit(work,i) for i in range(30)]
                    for future in futures:
                        try: latencies.append(future.result(timeout=20))
                        except Exception: failures+=1
                elapsed=time.perf_counter()-started
                stats=pool_experiment.snapshot() if mn else {}
                results.append({"repeat":repeat,"mode":label,"completed":len(latencies),
                    "errors":failures,"p50_ms":percentile(latencies,50) if latencies else None,
                    "p95_ms":percentile(latencies,95) if latencies else None,
                    "actual_completed_rps":round(len(latencies)/elapsed,2),
                    "pool_peak_active":stats.get("peak_active") if mn else None})
                if failures: raise RuntimeError("Hosted validation failure; adoption blocked")
            finally:
                os.environ["NAIJA_BITES_EXPERIMENT_POOL_ENABLED"]="0"
                if pool_experiment.pool_is_open(): pool_experiment.close_pool()
    from scripts.hosted_pool_integrity import run_integrity
    integrity = run_integrity()
    passed = all(case["status"] == "passed" for case in integrity)
    return {"status":"passed" if passed else "blocked",
            "pg_major":18,"cases":results,"integrity":integrity,
            "note":"Fictional read and transactional workload; no external providers; no customer data"}

_result={"status":"running"}
def worker():
    global _result
    try: _result=run()
    except Exception as exc:
        # No SQL, DB URL, credentials, or exception details in public output.
        _result={"status":"failed","reason_type":type(exc).__name__}
        print("HOSTED_VALIDATION_FAILED",type(exc).__name__,flush=True)
    else:
        print("HOSTED_VALIDATION_" + ("PASS" if _result["status"]=="passed" else "BLOCKED"),
              json.dumps(_result,sort_keys=True),flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/healthz","/"):
            self.send_error(404);return
        # Avoid publishing benchmarking details or project metadata publicly.
        payload=json.dumps({"status":_result["status"]}).encode()
        self.send_response(200 if _result["status"]=="passed" else 503)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self,*args): pass

if __name__=="__main__":
    pool_experiment.guard()
    threading.Thread(target=worker,daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","10000"))),Handler).serve_forever()
