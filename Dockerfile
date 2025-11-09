# syntax=docker/dockerfile:1

# Stage 0: get the Lambda Web Adapter binary
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 AS lambda-adapter

# Stage 1: AWS Lambda Python base image (includes the runtime bootstrap)
FROM public.ecr.aws/lambda/python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR ${LAMBDA_TASK_ROOT}

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (pricing must be a package; __init__.py must NOT import portal)
COPY pricing ./pricing

# Put the adapter into the image
COPY --from=lambda-adapter /lambda-adapter /lambda-adapter
RUN chmod +x /lambda-adapter

# Do NOT set AWS_LAMBDA_EXEC_WRAPPER here. We will control entrypoint/command via ImageConfig.
# Keep CMD harmless; Lambda's base image wants something here but we'll override it.
CMD ["pricing.lambda_handler.handler"]
