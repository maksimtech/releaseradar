FROM python:3.12-slim-trixie

# Metadata OCI
LABEL maintainer="maksimtech <github@maksimtech.com>"
LABEL org.opencontainers.image.title="ReleaseRadar"
LABEL org.opencontainers.image.description="GitHub repo lifecycle tracker — from issue open to release published"
LABEL org.opencontainers.image.source="https://github.com/maksimtech/releaseradar"
LABEL org.opencontainers.image.licenses="MIT"

# System packages, upgraded at build time: a Debian fix reaches the image
# through a rebuild and nobody has to chase it. Nothing is added: the CLI and
# the MCP server need Python alone.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Python environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Where the package comes from:
#   local (default, CI) → the code in this repository
#   pypi                → releaseradar==RELEASERADAR_VERSION from the index
ARG RELEASERADAR_SOURCE=local
ARG RELEASERADAR_VERSION=

# Upgrade pip: the copy in the base image has known CVEs.
RUN pip install --no-cache-dir --root-user-action=ignore -U pip

COPY pyproject.toml README.md LICENSE /app/build/
COPY releaseradar/ /app/build/releaseradar/

# `[mcp]` on both branches: the extra is optional on PyPI, where a cron job
# running `releaseradar sync` wants none of the MCP SDK, and present in the
# image, where `docker run -i … mcp` has to be a working server.
RUN case "${RELEASERADAR_SOURCE}" in \
        local) pip wheel --no-deps --no-cache-dir --wheel-dir /app/wheel /app/build && \
               WHEEL="$(ls /app/wheel/*.whl)" && \
               { pip install --no-cache-dir --root-user-action=ignore --only-binary :all: "${WHEEL}[mcp]" || \
                 pip install --no-cache-dir --root-user-action=ignore "${WHEEL}[mcp]" ; } ;; \
        pypi) test -n "${RELEASERADAR_VERSION}" || { echo "RELEASERADAR_VERSION is required with RELEASERADAR_SOURCE=pypi" >&2; exit 1; } && \
              { pip install --no-cache-dir --root-user-action=ignore --only-binary :all: "releaseradar[mcp]==${RELEASERADAR_VERSION}" || \
                pip install --no-cache-dir --root-user-action=ignore "releaseradar[mcp]==${RELEASERADAR_VERSION}" ; } ;; \
        *) echo "RELEASERADAR_SOURCE must be 'local' or 'pypi'" >&2; exit 1 ;; \
    esac && \
    rm -rf /app/build /app/wheel

# Remove pip: the runtime does not need it, and the packages vendored inside it
# carry CVEs that no pin of ours can reach.
RUN pip uninstall pip -y --root-user-action=ignore

# A non-root user to run as
RUN useradd -m -u 1000 releaseradar && \
    mkdir -p /home/releaseradar/.releaseradar && \
    chown -R releaseradar:releaseradar /home/releaseradar

USER releaseradar

# SQLite state DB lives here
VOLUME ["/home/releaseradar/.releaseradar"]
WORKDIR /home/releaseradar/.releaseradar

# `releaseradar track owner/repo`, `releaseradar sync`, or — with `-i` — the
# MCP server: `docker run -i -v …:/home/releaseradar/.releaseradar maksimtech/releaseradar mcp`
ENTRYPOINT ["releaseradar"]
CMD ["--help"]
