FROM python:3.12-slim

WORKDIR /app

# Install git for pip VCS installs and clean up apt lists
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install "git+https://github.com/project-unisonOS/unison-common.git@main" \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir redis python-jose[cryptography] bleach httpx

COPY src ./src

ENV PYTHONPATH=/app/src

EXPOSE 8081
CMD ["python", "src/server.py"]
