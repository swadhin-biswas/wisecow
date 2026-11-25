FROM debian:bookworm-slim

ARG GIT_SHA=dev

LABEL org.opencontainers.image.title="wisecow" \
      org.opencontainers.image.description="Cow wisdom served over HTTP" \
      org.opencontainers.image.source="https://github.com/swadhinbiswas/wisecow" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PATH="/usr/games:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cowsay \
        fortune-mod \
        fortunes-min \
        netcat-openbsd \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 wisecow \
    && useradd --system --uid 10001 --gid wisecow --home-dir /app --shell /usr/sbin/nologin wisecow

COPY wisecow.sh /opt/wisecow/wisecow.sh
RUN chmod 0755 /opt/wisecow/wisecow.sh \
    && mkdir -p /app \
    && chown wisecow:wisecow /app

WORKDIR /app
USER 10001:10001

EXPOSE 4499

# The app is a plain TCP listener, so a TCP connect is the cheapest real check.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/4499"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/wisecow/wisecow.sh"]
