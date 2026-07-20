FROM node:22-alpine AS frontend

WORKDIR /app/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY artcalc/ ./artcalc/
COPY data/solver_catalog.json ./data/solver_catalog.json
COPY web/__init__.py ./web/__init__.py
COPY web/backend/ ./web/backend/
COPY --from=frontend /app/web/frontend/dist ./web/frontend/dist

EXPOSE 8000
CMD ["uvicorn", "web.backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
