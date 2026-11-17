FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PULSE_ACCESS=cloud PULSE_DB=/data/market.db PULSE_SCHEDULER=1
WORKDIR /app
COPY backend/requirements.lock backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock
COPY backend/ backend/
COPY --from=frontend /build/dist frontend/dist
RUN useradd --uid 10001 --create-home pulse && mkdir /data && chown pulse:pulse /data
EXPOSE 8080
CMD ["python", "-m", "backend.serve"]
