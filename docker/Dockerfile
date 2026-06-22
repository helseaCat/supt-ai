FROM public.ecr.aws/lambda/python:3.12

# Install git (required by PR-Agent's GitPython dependency)
RUN dnf install -y git && dnf clean all

# Install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy function code
COPY wrapper.py ${LAMBDA_TASK_ROOT}/
COPY lib/ ${LAMBDA_TASK_ROOT}/lib/
COPY config.toml ${LAMBDA_TASK_ROOT}/

CMD ["wrapper.handler"]
