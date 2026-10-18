# Interlatch's hub in a container (docs/docker.md). The release workflow builds it with INTERLATCH_VERSION set and
# publishes it as ghcr.io/chatixia-ai/interlatch-hub. Built without it, it installs the latest release from PyPI; a
# wheel in docker/wheels/ is installed instead, to try a change before it is released:
#   uv build --wheel -o docker/wheels && docker build -t interlatch-hub docker
FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ARG INTERLATCH_VERSION=
LABEL org.opencontainers.image.title="Interlatch hub" \
      org.opencontainers.image.description="A team's hub for Interlatch: what its coding agents learned, without the transcripts" \
      org.opencontainers.image.source="https://github.com/Chatixia-AI/interlatch" \
      org.opencontainers.image.licenses="MIT"

# The user keeps uid 1000, which owns /data in a volume made by an image from before the rename
COPY wheels/ /tmp/wheels/
RUN set -e; \
    wheel=$(find /tmp/wheels -name 'interlatch-*.whl' | head -n 1); \
    if [ -n "$wheel" ]; then spec="$wheel[team]"; \
    else spec="interlatch[team]${INTERLATCH_VERSION:+==$INTERLATCH_VERSION}"; fi; \
    pip install --no-cache-dir --root-user-action=ignore "$spec"; \
    rm -rf /tmp/wheels; \
    useradd --create-home --uid 1000 interlatch; \
    mkdir /data; \
    chown interlatch:interlatch /data

# INTERLATCH_CONTAINER: the dashboard's Update button says to pull a new image instead of upgrading in place.
# The hub still reads the CHRONICLE_* names a hub set up before the rename passes it; an INTERLATCH_* one wins.
ENV INTERLATCH_HOME=/data \
    INTERLATCH_CONTAINER=1 \
    PYTHONUNBUFFERED=1
USER interlatch
WORKDIR /data
VOLUME /data
EXPOSE 11524

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/' % (os.environ.get('INTERLATCH_PORT') or os.environ.get('CHRONICLE_PORT') or '11524'), timeout=4)"]

CMD ["interlatch", "container"]
