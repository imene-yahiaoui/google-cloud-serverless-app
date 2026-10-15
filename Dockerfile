# syntax=docker/dockerfile:1
# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install dependencies into a prefix so we can copy them cleanly
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY app/ app/
COPY scripts/ scripts/

# Non-root user for security (Cloud Run best practice)
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Cloud Run injects PORT; default to 8080
ENV PORT=8080

EXPOSE 8080

# Single worker is correct for Cloud Run (scale via instance count, not threads)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level warning"]
