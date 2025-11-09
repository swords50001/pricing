# syntax=docker/dockerfile:1

# --- Stage 0: fetch the Lambda Web Adapter binary ---
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# --- Stage 1: AWS Lambda Python base image (includes bootstrap/runtime) ---
FROM public.ecr.aws/lambda/python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR ${LAMBDA_TASK_ROOT}

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (pricing must be a package; __init__.py must NOT import portal)
COPY pricing ./pricing

# Put the adapter in the image
COPY --from=lambda-adapter /lambda-adapter /lambda-adapter
RUN chmod +x /lambda-adapter

# Tell Lambda to exec the adapter instead of the normal runtime (wrapper mode)
ENV AWS_LAMBDA_EXEC_WRAPPER=/lambda-adapter \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_LOG_LEVEL=debug

# IMPORTANT: The Lambda python entrypoint *requires* a "handler" argument.
# Provide any valid-looking handler string to satisfy it. The adapter will
# start your uvicorn command and proxy HTTP; the handler is not actually used.
CMD ["pricing.lambda_handler.handler"]
