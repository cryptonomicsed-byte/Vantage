# parrot-security scanner — minimal, single-purpose: ClamAV + YARA + binwalk
# against one file at a time. See README.md for the freshclam/network tradeoff.
FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        clamav clamav-daemon clamav-freshclam yara binwalk python3 python3-pip \
    && freshclam || true \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m parrot
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt
COPY server.py .
COPY rules/ /app/rules/
RUN mkdir -p /scan && chown -R parrot:parrot /app /scan /var/lib/clamav

USER parrot
EXPOSE 9878
CMD ["python3", "server.py"]
