FROM python:3.12-slim

ARG REPO_PATH="."
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY ${REPO_PATH}/constraints.txt ./constraints.txt
COPY ${REPO_PATH}/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -c ./constraints.txt git+https://github.com/project-unisonOS/unison-common.git@main \
    && pip install --no-cache-dir -c ./constraints.txt -r requirements.txt

COPY ${REPO_PATH}/src/ ./src/
COPY ${REPO_PATH}/tests ./tests

ENV PYTHONPATH=/app/src
EXPOSE 8081
CMD ["python", "src/server.py"]
