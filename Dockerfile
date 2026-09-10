FROM python:3.12-slim AS artcalc-api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY artcalc/ ./artcalc/
COPY data/solver_catalog.json ./data/solver_catalog.json
COPY web/__init__.py ./web/__init__.py
COPY web/backend/ ./web/backend/

RUN groupadd --gid 10001 artcalc \
    && useradd --uid 10001 --gid artcalc --no-create-home --shell /usr/sbin/nologin artcalc \
    && mkdir -p /app/data/feedback \
    && chown artcalc:artcalc /app/data/feedback

USER artcalc

EXPOSE 8000
STOPSIGNAL SIGTERM

CMD ["sh", "-c", "exec uvicorn web.backend.app:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]


FROM node:22-alpine AS frontend-build

WORKDIR /app/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build


FROM caddy:2-alpine AS gateway

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /app/web/frontend/dist /srv
