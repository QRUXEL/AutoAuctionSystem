# 1. Build the React web app
FROM node:22-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# vite writes to ../app/static, i.e. /build/app/static
RUN npm run build

# 2. The server
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY --from=web /build/app/static ./app/static
COPY branding ./branding-default
ENV DATA_DIR=/data BRANDING_DIR=/branding
VOLUME ["/data", "/branding"]
EXPOSE 8080 2121 30000-30009
# Seed the branding volume on first start so the logo and frame can be edited in place.
CMD ["sh", "-c", "[ -e /branding/frame.json ] || cp -r /srv/branding-default/. /branding/; exec uvicorn app.main:app --host 0.0.0.0 --port 8080"]
