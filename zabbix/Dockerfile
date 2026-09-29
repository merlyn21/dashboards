# Zabbix Galaxy: 3D dashboard for Zabbix.
# The backend uses only the Python standard library, so the image needs no pip install.
# The browser loads Three.js and fonts from public CDNs.
#
# Build:
#   docker build -t zabbix-galaxy .
#
# Run with a settings file (never baked into the image):
#   docker run -d --name galaxy -p 8080:8080 \
#     -v "$PWD/.env:/app/.env:ro" zabbix-galaxy
#   docker run -d --name galaxy-uk -p 8090:8080 \
#     -v "$PWD/.env_uk:/app/.env_uk:ro" zabbix-galaxy env_uk
#
# Or with environment variables only:
#   docker run -d -p 8080:8080 -e ZABBIX_URL=https://zabbix.example.com \
#     -e ZABBIX_USER=api -e ZABBIX_PASSWORD=secret -e GALAXY_LANG=en zabbix-galaxy

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Inside the container the server always listens on 0.0.0.0:8080; environment
    # variables override GALAXY_HOST / GALAXY_PORT from the settings file.
    # Publish it on any host port with -p <host-port>:8080.
    GALAXY_HOST=0.0.0.0 \
    GALAXY_PORT=8080

WORKDIR /app

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin galaxy

COPY --chown=galaxy:galaxy server.py ./
COPY --chown=galaxy:galaxy web/ ./web/

USER galaxy

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/config', timeout=4); sys.exit(0)" || exit 1

# Optional argument: settings file name (e.g. env_uk); default is /app/.env.
ENTRYPOINT ["python3", "server.py"]
CMD []
