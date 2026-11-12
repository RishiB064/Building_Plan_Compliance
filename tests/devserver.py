"""Run the whole service in ONE process with no Docker, no Redis and no model download.

  python tests/devserver.py            ->  http://localhost:8000

Fake Redis + the stub model + a hashed embedder. For previewing the UI and for tests only. Not the real thing:
the real thing is `docker compose up`.
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
STUB_PORT = int(os.environ.get("DEV_STUB_PORT", "18081"))
API_PORT = int(os.environ.get("DEV_API_PORT", "8000"))
os.environ.update({
    "LLM_BASE_URL": f"http://127.0.0.1:{STUB_PORT}/v1", "LLM_API_KEY": "dev", "LLM_MODEL": "stub",
    "STUB_DELAY_MS": os.environ.get("STUB_DELAY_MS", "400"), "EMBED_MODEL": "hashed-bow", "REDIS_URL": "redis://unused",
    "CORPUS_DIR": os.path.join(ROOT, "corpus"), "INDEX_DIR": tempfile.mkdtemp(),
    "PROMPTS_DIR": os.path.join(ROOT, "prompts"), "PROMPT_VERSION": os.environ.get("PROMPT_VERSION", "v1"),
    "MIN_RELEVANCE": "0.05", "RATE_LIMIT_PER_MIN": "1000", "SCORE_SAMPLE_RATE": "1.0",
})

import fakeredis  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
import uvicorn  # noqa: E402

from precheck import dev_embed  # noqa: E402

dev_embed.patch_index_module()
SERVER = fakeredis.FakeServer()
aioredis.from_url = lambda *a, **k: fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=True)

from precheck import index as idx  # noqa: E402
from precheck import stub_llm, worker  # noqa: E402

threading.Thread(target=uvicorn.Server(uvicorn.Config(stub_llm.app, host="127.0.0.1", port=STUB_PORT,
                                                      log_level="error")).run, daemon=True).start()
idx.build_index(os.environ["CORPUS_DIR"], os.environ["INDEX_DIR"], "hashed-bow")
INDEX = idx.Index(os.environ["INDEX_DIR"], "hashed-bow")
rsync = fakeredis.FakeRedis(server=SERVER, decode_responses=True)
threading.Thread(target=worker.loop, args=(rsync, INDEX), daemon=True).start()
time.sleep(1)

from precheck import api  # noqa: E402

if __name__ == "__main__":
    print(f"dev server on http://localhost:{API_PORT}  (stub on {STUB_PORT})", flush=True)
    uvicorn.run(api.app, host="0.0.0.0", port=API_PORT, log_level="warning")
