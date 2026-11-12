"""Local load-test rig, no Docker: real Redis server + stub model + API + N worker processes + Locust.

  python tests/loadrig.py --workers 3 --threads 1 --users 10 --seconds 150

Uses the stub model (default 1500 ms per call) and the hashed embedder, so it measures queueing and our own code,
not a real provider or the real embedding model. Numbers from Docker on your machine will differ: run Locust there too.
"""
import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--workers", type=int, default=1)
ap.add_argument("--threads", type=int, default=1)
ap.add_argument("--users", type=int, default=10)
ap.add_argument("--seconds", type=int, default=150)
ap.add_argument("--stub-ms", type=int, default=1500)
ap.add_argument("--out", default=os.path.join(ROOT, "results"))
a = ap.parse_args()

tmp = tempfile.mkdtemp()
env = dict(os.environ, LLM_BASE_URL="http://127.0.0.1:18090/v1", LLM_API_KEY="rig", LLM_MODEL="stub",
           STUB_DELAY_MS=str(a.stub_ms), EMBED_MODEL="hashed-bow", REDIS_URL="redis://127.0.0.1:6390/0",
           CORPUS_DIR=os.path.join(ROOT, "corpus"), INDEX_DIR=os.path.join(tmp, "index"),
           PROMPTS_DIR=os.path.join(ROOT, "prompts"), PROMPT_VERSION="v1", MIN_RELEVANCE="0.05",
           RATE_LIMIT_PER_MIN="100000", SCORE_SAMPLE_RATE="0", WORKER_CONCURRENCY=str(a.threads),
           OTEL_EXPORTER_OTLP_ENDPOINT="", PYTHONPATH=ROOT)
procs = []


def spawn(cmd, **kw):
    p = subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    procs.append(p)
    return p


try:
    spawn(["redis-server", "--port", "6390", "--save", "", "--appendonly", "no"])
    spawn([sys.executable, "-m", "uvicorn", "precheck.stub_llm:app", "--port", "18090", "--log-level", "error"])
    subprocess.run([sys.executable, "-c",
                    "from precheck import dev_embed; dev_embed.patch_index_module();"
                    "from precheck.index import build_index; from precheck.config import settings;"
                    "build_index(settings.corpus_dir, settings.index_dir, settings.embed_model)"],
                   env=env, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    for _ in range(a.workers):
        spawn([sys.executable, os.path.join(ROOT, "tests", "rig_worker.py")])
    spawn([sys.executable, os.path.join(ROOT, "tests", "rig_api.py")])
    import urllib.request
    for _ in range(60):  # wait until the API answers its health check
        try:
            urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    os.makedirs(a.out, exist_ok=True)
    prefix = os.path.join(a.out, f"w{a.workers}x{a.threads}_u{a.users}")
    subprocess.run(["locust", "-f", os.path.join(ROOT, "locustfile.py"), "--host", "http://127.0.0.1:8001", "--headless",
                    "-u", str(a.users), "-r", str(a.users), "-t", f"{a.seconds}s", "--csv", prefix, "--only-summary"],
                   env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(prefix + "_stats.csv") as f:
        rows = {r["Name"]: r for r in csv.DictReader(f)}
    r = rows.get("submit->result (end to end)")
    if r:
        print(f"workers={a.workers} threads={a.threads} users={a.users} runs={r['Request Count']} failures={r['Failure Count']} "
              f"p50={float(r['50%'])/1000:.1f}s p95={float(r['95%'])/1000:.1f}s max={float(r['Max Response Time'])/1000:.1f}s")
    else:
        print("no completed runs", list(rows))
finally:
    for p in procs:
        p.terminate()
    time.sleep(3)  # let ports free up before the next scenario
    shutil.rmtree(tmp, ignore_errors=True)
