"""One worker process for the local load rig (no Docker). Same code path as precheck.worker.main, minus the
fixed metrics port so several can run on one machine, and with the hashed embedder (dev only)."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from precheck import dev_embed  # noqa: E402

dev_embed.patch_index_module()
from precheck import worker  # noqa: E402
from precheck.config import settings  # noqa: E402
from precheck.index import Index  # noqa: E402

index = Index(settings.index_dir, settings.embed_model)
for _ in range(settings.worker_concurrency - 1):
    threading.Thread(target=worker.loop, args=(worker.make_redis(), index), daemon=True).start()
worker.loop(worker.make_redis(), index)
