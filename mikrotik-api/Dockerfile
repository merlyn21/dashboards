# MikroTik connection map: one small container, standard library Python only.
# Databases (DB-IP, IEEE OUI) are not baked in: mount the project's ./geo and ./oui folders
# (docker-compose.yml does it); start.py downloads whatever is missing there on the first start.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8787

WORKDIR /app
COPY *.py ./
COPY web/ web/

RUN useradd --system --uid 10001 --home-dir /app app \
 && mkdir -p geo oui \
 && chown app:app geo oui
USER app

EXPOSE 8787

HEALTHCHECK --interval=60s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"HTTP_PORT\", \"8787\")}/api/state', timeout=4)" || exit 1

CMD ["python", "start.py"]
