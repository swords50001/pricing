# syntax=docker/dockerfile:1

# --- Stage 0: get the Lambda Web Adapter binary (no curl needed) ---
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# --- Stage 1: your Lambda runtime image ---
FROM public.ecr.aws/lambda/python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR ${LAMBDA_TASK_ROOT}

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY pricing ./pricing

# Copy the adapter binary from stage 0
COPY --from=lambda-adapter /aws-lambda-adapter /aws-lambda-adapter
RUN chmod +x /aws-lambda-adapter

# Adapter configuration
ENV AWS_LAMBDA_EXEC_WRAPPER=/aws-lambda-adapter \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream

# Start FastAPI via Uvicorn (make sure pricing/portal.py defines `app`)
CMD ["uvicorn", "pricing.portal:app", "--host", "0.0.0.0", "--port", "8080"]
