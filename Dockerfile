# Multi-arch base image
FROM python:3.11-slim-bookworm

LABEL maintainer="TVBackupMatcher"
LABEL description="Web GUI to scan, OCR, match, and organize TV media backups with Dolby Vision P7 -> 8.1 support."

# Install core runtime dependencies: ffmpeg, tesseract OCR with English data, mkvtoolnix, mediainfo, gosu, curl, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    mkvtoolnix \
    mediainfo \
    curl \
    ca-certificates \
    gosu \
    tar \
    && rm -rf /var/lib/apt/lists/*

# Install dovi_tool (architecture-aware: amd64 -> x86_64, arm64 -> aarch64)
ARG DOVI_TOOL_VERSION=2.3.4
RUN set -eux; \
    dpkg_arch="$(dpkg --print-architecture)"; \
    case "${dpkg_arch}" in \
        amd64) dovi_arch="x86_64" ;; \
        arm64) dovi_arch="aarch64" ;; \
        *) echo "Unsupported architecture for dovi_tool: ${dpkg_arch}"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_TOOL_VERSION}/dovi_tool-${DOVI_TOOL_VERSION}-${dovi_arch}-unknown-linux-musl.tar.gz" -o /tmp/dovi_tool.tar.gz; \
    tar -xzf /tmp/dovi_tool.tar.gz -C /usr/local/bin; \
    chmod +x /usr/local/bin/dovi_tool; \
    rm /tmp/dovi_tool.tar.gz

# Install dovi_convert.py
RUN set -eux; \
    curl -fsSL "https://raw.githubusercontent.com/cryptochrome/dovi_convert/master/dovi_convert.py" -o /usr/local/bin/dovi_convert; \
    chmod +x /usr/local/bin/dovi_convert; \
    ln -sf /usr/local/bin/dovi_convert /usr/local/bin/dovi_convert.py

# Create persistent mount points and app workspace
WORKDIR /app
RUN mkdir -p /media /config /export /cache

# Copy application source code
COPY app.py /app/app.py
COPY tv_matcher /app/tv_matcher
COPY server /app/server
COPY web /app/web
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    TVMATCHER_HOME=/config \
    HOST=0.0.0.0 \
    PORT=8765 \
    NO_BROWSER=1 \
    PUID=1000 \
    PGID=1000 \
    UMASK=002

EXPOSE 8765

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python3", "/app/app.py", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
