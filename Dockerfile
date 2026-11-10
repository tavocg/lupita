FROM node:22-bookworm-slim AS builder
ARG HUGO_VERSION=0.166.0
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl librsvg2-bin fonts-dejavu-core \
    && curl -fsSL "https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_${HUGO_VERSION}_linux-${TARGETARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin hugo \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /site
COPY hugo.toml ./
COPY content/ content/
COPY themes/ themes/
COPY scripts/build.sh scripts/build.sh
ARG HUGO_BASEURL=https://lupitacr.com/
RUN sh scripts/build.sh /site/output --gc --environment production --baseURL "$HUGO_BASEURL"

FROM nginx:stable-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /site/output/ /usr/share/nginx/html/
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
