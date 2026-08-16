# Code sandbox — where agent-authored code actually runs.
#
# Deliberately NOT on the internal-only network that pine-runtime and
# parrot-security use: cloning a repository and installing its dependencies
# both need egress, and a sandbox that cannot do either cannot do this job.
# The isolation that remains is the container itself (non-root, dropped caps,
# no-new-privileges, pid/memory/cpu caps) plus a workspace volume that is the
# only writable path. See docker-compose.yml.
#
# Toolchains are baked in rather than installed per session so a task does not
# start with a five-minute apk run. Add a coding CLI here (opencode, aider, or
# whatever you standardise on) and it becomes available to `exec` immediately —
# nothing in Vantage needs to change, because exec is generic.
FROM node:20-alpine

RUN apk add --no-cache \
      git \
      bash \
      curl \
      python3 \
      py3-pip \
      ripgrep \
      jq \
      openssh-client \
    && rm -rf /var/cache/apk/*

WORKDIR /app
COPY server.js ./

# Non-root, and the workspace is owned by that user so it is the only place
# anything can be written.
RUN mkdir -p /workspace && chown -R node:node /workspace /app
USER node

ENV WORKSPACE_ROOT=/workspace \
    SANDBOX_PORT=9880 \
    npm_config_update_notifier=false

EXPOSE 9880
CMD ["node", "server.js"]
