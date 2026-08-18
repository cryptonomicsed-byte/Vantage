# Slim replacement for parrotsec/security:latest (17.3GB) — same 8 CLI tools
# actually invoked by Vantage/pipeline/okf_pipeline.py + parrot_tools.py via
# `docker exec ares-parrot <tool>`, on a minimal Debian base instead of the
# full ParrotOS desktop distro. See incident-report-2026-07-15 (local memory)
# and the Claude-Codex vault correction note for the audit that led here.
FROM debian:bookworm-slim

# sqlmap/gobuster/nmap/ffuf/whatweb/wafw00f/dirb are ordinary Debian packages.
# nikto (Perl) and commix (Python) aren't in Debian's repos — cloned directly
# from upstream below rather than adding a third-party apt repo.
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlmap \
    gobuster \
    nmap \
    ffuf \
    whatweb \
    wafw00f \
    dirb \
    ca-certificates \
    git \
    perl \
    libjson-perl \
    libxml-writer-perl \
    python3 \
    python3-requests \
    python3-bs4 \
    && rm -rf /var/lib/apt/lists/*

# okf_pipeline.py/parrot_tools.py hardcode /usr/share/wordlists/dirb/common.txt
# (ParrotOS's layout); Debian's dirb package installs to /usr/share/dirb/wordlists/.
RUN mkdir -p /usr/share/wordlists && ln -s /usr/share/dirb/wordlists /usr/share/wordlists/dirb

# nikto — https://github.com/sullo/nikto
RUN git clone --depth 1 https://github.com/sullo/nikto.git /opt/nikto \
    && printf '#!/bin/sh\nexec perl /opt/nikto/program/nikto.pl "$@"\n' > /usr/local/bin/nikto \
    && chmod +x /usr/local/bin/nikto

# commix — https://github.com/commixproject/commix
RUN git clone --depth 1 https://github.com/commixproject/commix.git /opt/commix \
    && printf '#!/bin/sh\nexec python3 /opt/commix/commix.py "$@"\n' > /usr/local/bin/commix \
    && chmod +x /usr/local/bin/commix

CMD ["tail", "-f", "/dev/null"]
