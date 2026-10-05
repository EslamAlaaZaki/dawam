# One image for both the `app` and `worker` services: the FastAPI backend. The web UI
# is the separate `web` service (frontend/Dockerfile).

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip install . && rm -rf src
RUN useradd --system --uid 10001 --no-create-home dawam \
    && mkdir -p /var/lib/dawam/files \
    && chown dawam /var/lib/dawam/files
USER dawam
EXPOSE 8000
CMD ["python", "-m", "dawam", "serve"]
