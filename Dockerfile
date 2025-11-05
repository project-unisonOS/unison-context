FROM python:3.12-slim

WORKDIR /app

COPY unison-context/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir redis python-jose[cryptography] bleach httpx

COPY unison-context/src ./src
# Provide shared library for imports
COPY unison-common/src/unison_common ./src/unison_common

ENV PYTHONPATH=/app/src

EXPOSE 8081
CMD ["python", "src/server.py"]
