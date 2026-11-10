# Sandbox image for ebook-sanitizer.
#
# It holds the two stages. The host runs each one in its own locked-down
# container with no network, no shared folders and no root.
#   render.py   opens the untrusted book and draws each page as raw pixels
#   rebuild.py  turns those pixels into a new PDF and adds OCR text
#
# Build it with:  ./ebook-sanitizer setup
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --root-user-action=ignore PyMuPDF==2.1

# OMP_THREAD_LIMIT=1: OCR runs one worker process per page, so each
# worker must stay on one thread.
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    OMP_THREAD_LIMIT=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY ebook_sanitizer/protocol.py container/render.py container/rebuild.py /app/

USER 65534:65534
WORKDIR /tmp
