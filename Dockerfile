# syntax=docker/dockerfile:1

# --- Stage 0: fetch the Lambda Web Adapter binary ---
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# --- Stage 1: run on plain Python, adapter is PID 1 ---
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Minimal OS deps (add more only if build errors show up)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY pricing ./pricing

# Adapter binary
COPY --from=lambda-adapter /lambda-adapter /aws-lambda-adapter
RUN chmod +x /aws-lambda-adapter

# Adapter config
ENV PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_LOG_LEVEL=debug

# Adapter is the entrypoint; it will exec whatever is in CMD
ENTRYPOINT ["/aws-lambda-adapter"]

# TEMP: boot the minimal app first (prove the stack)
CMD ["python","-m","uvicorn","pricing.health_app:app","--host","0.0.0.0","--port","8080","--proxy-headers","--log-level","info"]
