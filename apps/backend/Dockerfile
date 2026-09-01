FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG TYPST_VERSION=0.15.1
RUN curl -fsSL -o /tmp/typst.tar.xz \
      "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
    && tar -xJf /tmp/typst.tar.xz -C /tmp \
    && mv /tmp/typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst \
    && rm -rf /tmp/typst.tar.xz /tmp/typst-x86_64-unknown-linux-musl \
    && typst --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY typst ./typst
COPY fonts ./fonts
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts
RUN chmod +x /app/scripts/start-backend.sh

# Optional Noto Serif CJK TC/SC (~16 MiB each). If GitHub is unreachable, the
# image still builds; place the OTFs in /app/fonts (see fonts/README.md).
# New Computer Modern is bundled with the Typst binary.
RUN mkdir -p /app/fonts \
    && ( curl -fsSL -o /app/fonts/NotoSerifCJKtc-Regular.otf \
           "https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/TraditionalChinese/NotoSerifCJKtc-Regular.otf" \
         && curl -fsSL -o /app/fonts/NotoSerifCJKsc-Regular.otf \
           "https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf" ) \
    || echo "CJK fonts not downloaded at build time; add Noto Serif CJK TC/SC under /app/fonts"

ENV TYPST_PACKAGE_PATH=/app/typst/packages
ENV TYPST_FONT_PATHS=/app/fonts
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/healthz || exit 1

CMD ["/app/scripts/start-backend.sh"]
