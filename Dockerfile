FROM node:24-bookworm-slim AS node
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LYARA_PROVIDER=api \
    LYARA_WORKSPACE=/workspace \
    LYARA_DATA_DIR=/data

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
# JavaScript repairs use node --check; uploaded projects are never executed here.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY pyproject.toml requirements.txt ./
COPY lyara ./lyara
RUN python -m pip install --no-cache-dir . && mkdir -p /workspace

EXPOSE 8080
CMD ["python", "-m", "lyara", "serve", "--host", "0.0.0.0", "--port", "8080"]
