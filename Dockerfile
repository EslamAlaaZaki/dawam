# One image for both the `app` and `worker` services: the FastAPI backend. The web UI
# is the separate `web` service (frontend/Dockerfile).
#
# ACCELERATOR picks the build for document AI (Docling layout/table models, RapidOCR):
#   cpu  (default)  torch from the PyTorch CPU index + onnxruntime; no NVIDIA packages
#   cuda            CUDA torch + onnxruntime-gpu; run the worker with an NVIDIA GPU
#                   (deploy/docker-compose.gpu.yaml)
# The models are downloaded here, at build time, so nothing is downloaded at runtime.

FROM python:3.12-slim
ARG ACCELERATOR=cpu
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCLING_ARTIFACTS_PATH=/opt/dawam/models
WORKDIR /app

# torch first, so the dependencies below keep it (plain PyPI torch is the CUDA build).
RUN case "$ACCELERATOR" in \
      cpu) pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu ;; \
      cuda) pip install torch torchvision ;; \
      *) echo "ACCELERATOR must be cpu or cuda, not '$ACCELERATOR'" >&2; exit 1 ;; \
    esac

# The dependencies, on their own layer (a stub package stands in for the source), so a
# source change does not download them again.
COPY backend/pyproject.toml ./
RUN mkdir -p src/dawam && touch src/dawam/__init__.py \
    && if [ "$ACCELERATOR" = cuda ]; then ocr=ocr-gpu; else ocr=ocr-cpu; fi \
    && pip install ".[$ocr]" \
    && pip uninstall -y dawam && rm -rf src \
    && if [ "$ACCELERATOR" = cpu ] && pip list --format=freeze | grep -iE '^(nvidia-|onnxruntime-gpu|triton)'; then \
         echo "the CPU build must not contain NVIDIA/CUDA packages" >&2; exit 1; \
       fi

# Layout and table models, and RapidOCR's English and Arabic models (DoclingOcr reads each
# scanned page once per language), read from DOCLING_ARTIFACTS_PATH at runtime. Only the
# ones pdf_pipeline_options pins are kept (Heron on transformers, TableFormer accurate): the
# download also fetches Heron's ONNX export and TableFormer fast, removed in this same layer
# (`rm -r`, not -f, so a changed download layout fails the build).
RUN docling-tools models download layout tableformer rapidocr \
      --rapidocr-backend-lang onnxruntime:en --rapidocr-backend-lang onnxruntime:arabic \
      -o "$DOCLING_ARTIFACTS_PATH" \
    && cd "$DOCLING_ARTIFACTS_PATH" \
    && rm -r docling-project--docling-layout-heron-onnx \
             docling-project--docling-models/model_artifacts/tableformer/fast \
    && rm -rf */.cache \
    && chmod -R a+rX /opt/dawam \
    && rm -rf /root/.cache
ENV HF_HUB_OFFLINE=1

# Fonts for the PDF renderer (pdfium): a PDF that names a font without embedding it (e.g.
# Helvetica) renders without glyphs on a fontless image, and the layout model then reads its
# text as a picture. Liberation is metric-compatible with Helvetica/Times/Courier; DejaVu
# covers Arabic.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY backend/src ./src
RUN pip install --no-deps . && rm -rf src
RUN useradd --system --uid 10001 --create-home dawam \
    && mkdir -p /var/lib/dawam/files \
    && chown dawam /var/lib/dawam/files
USER dawam
EXPOSE 8000
CMD ["python", "-m", "dawam", "serve"]
