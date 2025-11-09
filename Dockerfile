# syntax=docker/dockerfile:1

# --- Stage 0: fetch the Lambda Web Adapter binary ---
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# --- Stage 1: Lambda runtime image ---
FROM public.ecr.aws/lambda/python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR ${LAMBDA_TASK_ROOT}

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY pricing ./pricing

# Copy adapter binary and make it executable
COPY --from=lambda-adapter /lambda-adapter /aws-lambda-adapter
RUN chmod +x /aws-lambda-adapter

# Adapter config
ENV PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream

# 👇 This is the key change: make the adapter the container entrypoint
ENTRYPOINT ["/aws-lambda-adapter"]

# Start FastAPI via Uvicorn (module:variable must resolve)
CMD ["uvicorn", "pricing.portal:app", "--host", "0.0.0.0", "--port", "8080"]
