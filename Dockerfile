FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# CPU-only torch keeps the image small; sentence-transformers runs the embedder locally (free).
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-fetch the embedding model so containers start offline. Model id is injected, not hard-coded (D4).
ARG EMBED_MODEL
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBED_MODEL'])"

COPY precheck ./precheck
COPY prompts ./prompts
COPY web ./web
COPY eval ./eval
CMD ["python", "-m", "precheck.cli", "index-info"]
