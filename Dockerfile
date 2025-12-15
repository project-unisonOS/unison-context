# For meta-repo builds; pins base image
FROM ghcr.io/project-unisonos/unison-common-wheel:latest AS common_wheel
FROM python:3.12-slim@sha256:fdab368dc2e04fab3180d04508b41732756cc442586f708021560ee1341f3d29

ARG REPO_PATH="."
WORKDIR /app
COPY --from=common_wheel /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=common_wheel /usr/local/lib/python3.12/dist-packages /usr/local/lib/python3.12/dist-packages
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "src/server.py"]
