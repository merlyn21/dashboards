# Connection map in a container. It only works with the host's network, PIDs and file system:
# see docker-compose.yml for the required options and README.md, section Docker.
FROM python:3.12-slim-bookworm

# conntrack: table and events; iproute2: ip, ss; iptables: iptables-save in both nft and legacy variants
RUN apt-get update \
 && apt-get install -y --no-install-recommends conntrack iproute2 iptables \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY *.py start.sh .env.example names.conf.example ./
COPY web/ web/

# The host's root file system is mounted read-only here; /etc/os-release, /etc/hosts, the disk
# and OpenVPN status files are read through it.
ENV HOST_ROOT=/host PYTHONUNBUFFERED=1

# Databases live in the mounted geo/ and oui/ folders; missing ones are downloaded on start.
CMD ["python", "start.py"]
