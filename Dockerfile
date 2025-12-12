# For meta-repo builds; pins base image
FROM ghcr.io/project-unisonos/unison-common-wheel:latest AS common_wheel
FROM python:3.14-slim@sha256:2751cbe93751f0147bc1584be957c6dd4c5f977c3d4e0396b56456a9fd4ed137

ARG REPO_PATH="unison-context"
WORKDIR /app
COPY --from=common_wheel /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=common_wheel /usr/local/lib/python3.12/dist-packages /usr/local/lib/python3.12/dist-packages
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "src/server.py"]
