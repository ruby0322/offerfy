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

# The free families the template survey's font gate needs. A template that
# asks for a font this image lacks emits a warning rather than an error, so
# it compiles while rendering icons as tofu -- the survey therefore excludes
# it, and the picker loses the template. These twelve are what 15 otherwise
# passing templates were being excluded for, and they are the reason the
# offered list has typographic variety rather than New Computer Modern all
# the way down. Fetched from raw.githubusercontent.com on purpose: the
# github.com/<org>/<repo>/raw/ path 504s intermittently.
#
# Same tolerance as the CJK block above -- the image still builds without
# them, it just offers fewer templates. Keep in step with the host install
# recorded in docs/deploy.md: this image is NOT what serves offerfy.cc.
RUN mkdir -p /app/fonts \
    && GF=https://raw.githubusercontent.com/google/fonts/main \
    && FA=https://raw.githubusercontent.com/FortAwesome/Font-Awesome \
    && ( for u in \
           "$GF/ofl/inriasans/InriaSans-Regular.ttf" \
           "$GF/ofl/inriasans/InriaSans-Bold.ttf" \
           "$GF/ofl/inriasans/InriaSans-Italic.ttf" \
           "$GF/ofl/cantarell/Cantarell-Regular.ttf" \
           "$GF/ofl/cantarell/Cantarell-Bold.ttf" \
           "$GF/ofl/cantarell/Cantarell-Italic.ttf" \
           "$GF/ofl/ptserif/PT_Serif-Web-Regular.ttf" \
           "$GF/ofl/ptserif/PT_Serif-Web-Bold.ttf" \
           "$GF/ofl/ptserif/PT_Serif-Web-Italic.ttf" \
           "$GF/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf" \
           "$GF/ofl/montserrat/Montserrat%5Bwght%5D.ttf" \
           "$GF/ofl/opensans/OpenSans%5Bwdth%2Cwght%5D.ttf" \
           "$GF/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf" \
           "$GF/ofl/roboto/Roboto-Italic%5Bwdth%2Cwght%5D.ttf" \
           "$GF/ofl/sourcesans3/SourceSans3%5Bwght%5D.ttf" \
           "$GF/ofl/sourceserif4/SourceSerif4%5Bopsz%2Cwght%5D.ttf" \
           "$GF/ofl/librebaskerville/LibreBaskerville%5Bwght%5D.ttf" \
           "$GF/ofl/librebaskerville/LibreBaskerville-Italic%5Bwght%5D.ttf" \
           "$FA/6.x/otfs/Font%20Awesome%206%20Free-Regular-400.otf" \
           "$FA/6.x/otfs/Font%20Awesome%206%20Free-Solid-900.otf" \
           "$FA/6.x/otfs/Font%20Awesome%206%20Brands-Regular-400.otf" \
           "$FA/7.x/otfs/Font%20Awesome%207%20Free-Regular-400.otf" \
           "$FA/7.x/otfs/Font%20Awesome%207%20Free-Solid-900.otf" \
           "$FA/7.x/otfs/Font%20Awesome%207%20Brands-Regular-400.otf" \
         ; do \
           n=$(basename "$u" | sed 's/%20/ /g; s/%5B/[/g; s/%5D/]/g; s/%2C/,/g') \
           && curl -fsSL -o "/app/fonts/$n" "$u" || echo "skipped $n" ; \
         done ) \
    || echo "survey fonts not downloaded at build time; see docs/deploy.md"

ENV TYPST_PACKAGE_PATH=/app/typst/packages
ENV TYPST_FONT_PATHS=/app/fonts
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/healthz || exit 1

CMD ["/app/scripts/start-backend.sh"]
