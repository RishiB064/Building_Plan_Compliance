"""Redis key layout shared by api and worker."""
import hashlib
import json

QUEUE = "queue:runs"
QUARANTINE = "quarantine"
FEEDBACK = "feedback"
SCORES = "scores"


def run_key(run_id: str) -> str: return f"run:{run_id}"
def trace_key(run_id: str) -> str: return f"run:{run_id}:trace"
def cache_key(h: str) -> str: return f"cache:{h}"


def cache_hash(proposal: dict, prompt_version: str, index_version: str) -> str:
    canon = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(f"{canon}|{prompt_version}|{index_version}".encode()).hexdigest()
