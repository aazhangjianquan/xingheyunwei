# API-only image. The frontend has its own Dockerfile and release lifecycle.
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONPATH=/app
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security
RUN set -eux; \
    for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do \
        [ -f "$source_file" ] || continue; \
        sed -E -i \
            -e "s|https?://deb\.debian\.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|https?://security\.debian\.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|https?://deb\.debian\.org/debian|${DEBIAN_MIRROR}|g" \
            "$source_file"; \
    done; \
    printf '%s\n' 'Acquire::Retries "3";' 'Acquire::http::Timeout "30";' 'Acquire::https::Timeout "30";' > /etc/apt/apt.conf.d/80-network-retries; \
    apt-get update; \
    apt-get install -y --no-install-recommends openjdk-17-jre-headless libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-cjk; \
    rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/license-tool ./license-tool
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]

