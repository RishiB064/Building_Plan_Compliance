"""D2 / L1: corpus ingest, chunking, cached embeddings, FAISS index via LangChain.

Chunking strategy (addresses the section-3 difficulty):
  * Text corpora: one chunk per [CLAUSE-ID] block. Table rows are separate chunks and each repeats the
    table title and the row's key (e.g. plot-area band), so a row is retrievable on its own and still
    makes sense out of context.
  * PDFs: pypdf text per page, split at numbered clause starts; table rows that extract as one line stay
    one chunk. Page number is kept in metadata for citation.
  * Cross-reference metadata: ids mentioned inside a chunk (e.g. "Table T1") are stored in `refs`.
    Limits stated separately from tables are NOT linked from them, so the agent searches again using
    terms derived from what it has read (see agents.py) rather than relying on one similarity pass.

Re-embedding without a full rebuild: embeddings are cached on disk keyed by sha1(model + text).
Changing the corpus only embeds new chunks; changing EMBED_MODEL changes the cache key, so only then
is everything re-embedded. The FAISS index itself is cheap to reassemble from cached vectors.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

CLAUSE_HDR = re.compile(r"^\[(?P<id>[A-Za-z0-9][A-Za-z0-9._\-]*)\]\s*(?P<body>.*)$")
PDF_CLAUSE = re.compile(r"^\s*(?P<id>\d+(?:\.\d+){0,3})[.)]?\s+(?P<body>[A-Z].+)$")


def _sha(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------- parsing / chunking ----------

def parse_text(path: Path) -> list[dict]:
    chunks, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = CLAUSE_HDR.match(line.strip())
        if m:
            if cur:
                chunks.append(cur)
            cur = {"clause_id": m["id"], "text": m["body"].strip(), "source": path.name, "page": None}
        elif cur and line.strip():
            cur["text"] += " " + line.strip()
    if cur:
        chunks.append(cur)
    return chunks


def parse_pdf(path: Path) -> list[dict]:
    from pypdf import PdfReader

    chunks = []
    reader = PdfReader(str(path))
    for pno, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        cur = None
        for line in text.splitlines():
            m = PDF_CLAUSE.match(line)
            if m:
                if cur:
                    chunks.append(cur)
                cur = {"clause_id": f"{path.stem}:p{pno}:{m['id']}", "text": m["body"].strip(),
                       "source": path.name, "page": pno}
            elif cur and line.strip():
                cur["text"] += " " + line.strip()
        if cur:
            chunks.append(cur)
        if not text.strip():
            print(f"WARNING: {path.name} page {pno} has no text layer - OCR it first", flush=True)
    return chunks


def load_chunks(corpus_dir: str) -> tuple[list[dict], dict]:
    chunks, versions = [], {}
    for p in sorted(Path(corpus_dir).iterdir()):
        if p.suffix.lower() in {".txt", ".md"}:
            got = parse_text(p)
        elif p.suffix.lower() == ".pdf":
            got = parse_pdf(p)
        else:
            continue
        versions[p.name] = _sha(p.read_bytes().decode("latin-1"))[:12]
        chunks.extend(got)
    seen = set()
    for c in chunks:
        # disambiguate duplicate ids so every citation resolves to exactly one chunk
        base, n = c["clause_id"], 1
        while c["clause_id"] in seen:
            n += 1
            c["clause_id"] = f"{base}#{n}"
        seen.add(c["clause_id"])
        c["refs"] = sorted(set(re.findall(r"\b(?:Table|Clause)\s+([A-Z]+\d*(?:\.\w+)?)", c["text"])))
    return chunks, versions


# ---------- embeddings with a persistent cache ----------

class CachedEmbeddings(Embeddings):
    def __init__(self, inner: Embeddings, model_name: str, cache_path: str):
        self.inner, self.model_name, self.cache_path = inner, model_name, cache_path
        self.cache: dict[str, list[float]] = {}
        if os.path.exists(cache_path):
            self.cache = json.load(open(cache_path))
        self.hits = self.misses = 0

    def _key(self, text: str) -> str:
        return _sha(self.model_name, text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if self._key(t) not in self.cache]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        if missing:
            for t, v in zip(missing, self.inner.embed_documents(missing)):
                self.cache[self._key(t)] = [float(x) for x in v]
        return [self.cache[self._key(t)] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        k = self._key("q:" + text)
        if k not in self.cache:
            self.cache[k] = [float(x) for x in self.inner.embed_query(text)]
        return self.cache[k]

    def save(self):
        tmp = self.cache_path + ".tmp"
        json.dump(self.cache, open(tmp, "w"))
        os.replace(tmp, self.cache_path)


def make_embeddings(model_name: str, index_dir: str) -> CachedEmbeddings:
    from langchain_huggingface import HuggingFaceEmbeddings

    inner = HuggingFaceEmbeddings(model_name=model_name, encode_kwargs={"normalize_embeddings": True})
    return CachedEmbeddings(inner, model_name, os.path.join(index_dir, "embedding_cache.json"))


# ---------- build / load ----------

def build_index(corpus_dir: str, index_dir: str, model_name: str) -> dict:
    """Idempotent: same corpus + model -> same index. Returns the freshness metadata."""
    from langchain_community.vectorstores import FAISS

    t0 = time.time()
    os.makedirs(index_dir, exist_ok=True)
    chunks, versions = load_chunks(corpus_dir)
    if not chunks:
        raise SystemExit(f"No chunks parsed from {corpus_dir}. Put .txt/.md/.pdf files there.")
    emb = make_embeddings(model_name, index_dir)
    docs = [Document(page_content=c["text"], metadata={k: v for k, v in c.items() if k != "text"}) for c in chunks]
    ids = [c["clause_id"] for c in chunks]
    store = FAISS.from_documents(docs, emb, ids=ids)
    store.save_local(os.path.join(index_dir, "faiss"))
    emb.save()
    with open(os.path.join(index_dir, "chunks.jsonl"), "w") as f:
        for c in chunks:
            f.write(json.dumps(c) + "\n")
    secs = time.time() - t0
    meta = {
        "built_at": time.time(),
        "built_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus_versions": versions,
        "chunk_count": len(chunks),
        "embed_model": model_name,
        "embedding_cache_hits": emb.hits,
        "embedding_cache_misses": emb.misses,
        "build_seconds": round(secs, 3),
        "chunks_per_second": round(len(chunks) / secs, 2) if secs else None,
    }
    json.dump(meta, open(os.path.join(index_dir, "meta.json"), "w"), indent=2)
    return meta


class Index:
    """Read side used by agent tools."""

    def __init__(self, index_dir: str, model_name: str):
        from langchain_community.vectorstores import FAISS

        self.meta = json.load(open(os.path.join(index_dir, "meta.json")))
        self.chunks = {}
        for line in open(os.path.join(index_dir, "chunks.jsonl")):
            c = json.loads(line)
            self.chunks[c["clause_id"]] = c
        self.emb = make_embeddings(model_name, index_dir)
        self.store = FAISS.load_local(os.path.join(index_dir, "faiss"), self.emb,
                                      allow_dangerous_deserialization=True)  # our own artifact

    @property
    def version(self) -> str:
        return _sha(json.dumps(self.meta["corpus_versions"], sort_keys=True), self.meta["embed_model"])[:12]

    def search(self, query: str, k: int = 4) -> list[dict]:
        out = []
        # normalised embeddings + L2 index: cosine = 1 - d^2/2
        for doc, dist in self.store.similarity_search_with_score(query, k=k):
            cid = doc.metadata["clause_id"]
            out.append({"clause_id": cid, "text": self.chunks[cid]["text"],
                        "score": round(max(0.0, 1.0 - float(dist) / 2.0), 4),
                        "source": doc.metadata.get("source"), "page": doc.metadata.get("page")})
        return out

    def table_rows(self, number: str) -> list[dict]:
        """All rows of 'Table <number>': chunks whose id looks like <prefix>.T<number>.<row>."""
        return [{"clause_id": cid, "text": c["text"], "score": 1.0, "source": c.get("source"), "page": c.get("page")}
                for cid, c in self.chunks.items() if re.search(rf"\.T{number}\.", cid)]

    def get(self, clause_id: str) -> dict | None:
        c = self.chunks.get(clause_id)
        if not c:
            return None
        return {"clause_id": clause_id, "text": c["text"], "score": 1.0,
                "source": c.get("source"), "page": c.get("page")}
