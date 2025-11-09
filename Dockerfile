# syntax=docker/dockerfile:1

# --- Stage 0: fetch the Lambda Web Adapter binary ---
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# --- Stage 1: AWS Lambda Python base image ---
FROM public.ecr.aws/lambda/python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR ${LAMBDA_TASK_ROOT}

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code (pricing must be a package and **not** import portal in __init__.py)
COPY pricing ./pricing

# Put the adapter where the Lambda runtime expects the exec wrapper
COPY --from=lambda-adapter /lambda-adapter /lambda-adapter
RUN chmod +x /lambda-adapter

# Exec-wrapper mode: the adapter wraps the Lambda runtime and runs our CMD
ENV AWS_LAMBDA_EXEC_WRAPPER=/lambda-adapter \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_LOG_LEVEL=debug

# Single CMD: start FastAPI (portal exports `app`)
CMD ["uvicorn", "pricing.portal:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--log-level", "info"]
