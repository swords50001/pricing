# syntax=docker/dockerfile:1
FROM public.ecr.aws/lambda/python:3.11

# Make logs flush immediately
ENV PYTHONUNBUFFERED=1

# Work in Lambda task root
WORKDIR ${LAMBDA_TASK_ROOT}

# --- OS deps (curl for downloading the Web Adapter) ---
RUN yum -y install curl ca-certificates && yum clean all

# --- Python deps ---
# If you have requirements.txt at repo root, copy & install it first for better caching.
# (If you don't, create one; see note below.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- App code ---
# Copy your package into the Lambda task root so imports like `pricing.portal` work
COPY pricing ./pricing

# --- Lambda Web Adapter ---
RUN curl -L --fail -o /aws-lambda-adapter \
      https://github.com/awslabs/aws-lambda-web-adapter/releases/latest/download/aws-lambda-adapter \
 && chmod +x /aws-lambda-adapter

# Adapter configuration
ENV AWS_LAMBDA_EXEC_WRAPPER=/aws-lambda-adapter \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream

# --- Start FastAPI via Uvicorn (single CMD!) ---
# Your ASGI app lives at pricing/portal.py and is named `app`
CMD ["uvicorn", "pricing.portal:app", "--host", "0.0.0.0", "--port", "8080"]
