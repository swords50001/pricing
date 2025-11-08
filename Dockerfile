# syntax=docker/dockerfile:1
FROM public.ecr.aws/lambda/python:3.11

# Copy the pricing package into the Lambda task root.
COPY pricing ${LAMBDA_TASK_ROOT}/pricing

# Set the function handler for AWS Lambda.
CMD ["pricing.lambda_handler.handler"]
