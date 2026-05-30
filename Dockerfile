# ── Stage 1: Python dependencies ────────────────────────────────────────────
FROM python:3.12-alpine AS builder

WORKDIR /install
COPY app/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install/pkg -r requirements.txt


# ── Stage 2: Final image ──────────────────────────────────────────────────────
FROM python:3.12-alpine

# ffmpeg (includes ffprobe) – Alpine keeps this minimal (~30 MB)
RUN apk add --no-cache ffmpeg

# Copy installed Python packages
COPY --from=builder /install/pkg /usr/local

WORKDIR /app
COPY app/app.py .
COPY templates/ ../templates/

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

EXPOSE 5000

CMD ["python", "app.py"]
