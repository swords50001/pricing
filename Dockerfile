# syntax=docker/dockerfile:1
FROM public.ecr.aws/lambda/python:3.11

# Copy the pricing package into the Lambda task root.
COPY pricing ${LAMBDA_TASK_ROOT}/pricing

# Set the function handler for AWS Lambda.
CMD ["pricing.lambda_handler.handler"]

# Add the Lambda Web Adapter binary
ADD https://github.com/awslabs/aws-lambda-web-adapter/releases/latest/download/aws-lambda-adapter /aws-lambda-adapter
RUN chmod +x /aws-lambda-adapter

# These env vars tell Lambda to run the adapter and where your app listens
ENV AWS_LAMBDA_EXEC_WRAPPER=/aws-lambda-adapter
ENV PORT=8080

# Optional but nice: make health checks fast
ENV AWS_LWA_READINESS_CHECK_PATH=/health
ENV AWS_LWA_INVOKE_MODE=response_stream

# Your existing CMD is perfect (uvicorn on 0.0.0.0:8080)
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
