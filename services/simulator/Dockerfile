FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 simulator \
    && useradd --uid 10001 --gid simulator --no-create-home --shell /usr/sbin/nologin simulator

COPY app.py ./app.py

USER simulator

EXPOSE 8010
STOPSIGNAL SIGTERM

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers", "--forwarded-allow-ips=*"]
