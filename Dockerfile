# syntax=docker/dockerfile:1

# --- Stage 0: fetch the Lambda Web Adapter binary (official image) ---
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# --- Stage 1: run on plain Python, let the adapter be PID 1 ---
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# OS deps (lean). Add build deps only if your libs need them.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (make sure pricing/__init__.py exists and does NOT import portal)
COPY pricing ./pricing

# Copy adapter binary in as entrypoint
COPY --from=lambda-adapter /lambda-adapter /aws-lambda-adapter
RUN chmod +x /aws-lambda-adapter

# Adapter configuration
ENV PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_LOG_LEVEL=debug

# The adapter is the ENTRYPOINT; it launches whatever is in CMD
ENTRYPOINT ["/aws-lambda-adapter"]

# Start FastAPI via Uvicorn (portal app must export `app`)
CMD ["uvicorn", "pricing.portal:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--log-level", "info"]
