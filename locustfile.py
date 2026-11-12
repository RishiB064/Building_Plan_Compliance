"""D6: measure a run from submission to final result, not the submission call alone.

  locust -f locustfile.py --host http://localhost:8000 --headless -u 20 -r 2 -t 3m --csv results/run

Point the system at the stub first (LLM_BASE_URL in .env) so the test measures this system, not the provider.
Each simulated user uses its own X-Client-Id; raise RATE_LIMIT_PER_MIN for the test or the limiter will (correctly) reject.
Vary the proposals so the whole-run cache (D7) does not turn the test into a cache benchmark; set VARY=0 to measure the cached case.
"""
import os
import random
import time
import uuid

from locust import HttpUser, between, task

VARY = os.environ.get("VARY", "1") == "1"


class Client(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.cid = f"locust-{uuid.uuid4().hex[:6]}"

    @task
    def full_run(self):
        height = round(random.uniform(7, 14), 1) if VARY else 12
        body = {"plot_area_sqm": 100, "road_width_m": 5.5, "building_height_m": height, "num_floors": 3,
                "built_up_area_sqm": 200, "ground_coverage_pct": 70, "parking_spaces": 0,
                "building_use": "residential", "zone": "residential", "notes": ""}
        t0 = time.time()
        with self.client.post("/v1/runs", json=body, headers={"X-Client-Id": self.cid},
                              name="POST /v1/runs", catch_response=True) as r:
            if r.status_code != 202:
                r.failure(f"submit {r.status_code}")
                return
            run_id = r.json()["run_id"]
        status = "queued"
        while status in ("queued", "running") and time.time() - t0 < 300:
            time.sleep(1)
            with self.client.get(f"/v1/runs/{run_id}", name="GET /v1/runs/{id}", catch_response=True) as g:
                status = g.json()["status"]
        total_ms = (time.time() - t0) * 1000
        ok = status == "succeeded"
        self.environment.events.request.fire(
            request_type="RUN", name="submit->result (end to end)", response_time=total_ms,
            response_length=0, exception=None if ok else Exception(f"run ended {status}"), context={})
