import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from precheck import dev_embed  # noqa: E402

dev_embed.patch_index_module()
import uvicorn  # noqa: E402
from precheck import api  # noqa: E402

uvicorn.run(api.app, host="127.0.0.1", port=int(os.environ.get("RIG_API_PORT", "8001")), log_level="warning")
