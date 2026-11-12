"""Dev/test only: a tiny hashed bag-of-words embedder so tests and `eval --offline` run without downloading
sentence-transformers. Production uses the real model from EMBED_MODEL."""
import hashlib
import re

import numpy as np
from langchain_core.embeddings import Embeddings


class HashedBow(Embeddings):
    def _v(self, t):
        v = np.zeros(256, dtype="float32")
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
        n = np.linalg.norm(v)
        return (v / n if n else v).tolist()

    def embed_documents(self, texts):
        return [self._v(t) for t in texts]

    def embed_query(self, t):
        return self._v(t)


def patch_index_module():
    """Make precheck.index build/load with the hashed embedder."""
    import os

    from . import index as idx
    idx.make_embeddings = lambda model, d: idx.CachedEmbeddings(HashedBow(), model, os.path.join(d, "embedding_cache.json"))
