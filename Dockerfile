# One image for both the `app` and `worker` services.

FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip install . && rm -rf src
COPY --from=frontend /frontend/dist ./frontend
RUN useradd --system --uid 10001 --no-create-home dawam
USER dawam
ENV DAWAM_FRONTEND_DIST=/app/frontend
EXPOSE 8000
CMD ["python", "-m", "dawam", "serve"]
