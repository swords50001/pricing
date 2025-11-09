# syntax=docker/dockerfile:1
FROM public.ecr.aws/lambda/python:3.11

ENV PYTHONUNBUFFERED=1
WORKDIR ${LAMBDA_TASK_ROOT}

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY pricing ./pricing

# IMPORTANT: For Lambda base images, the CMD is the Python handler "module.function"
# We point at pricing/portal.py:handler created by Mangum
CMD ["pricing.portal.handler"]
