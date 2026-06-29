# Pine-runtime sandbox — pure Node, no external deps. Runs as non-root.
FROM node:20-alpine

WORKDIR /app
COPY package.json ./
# No third-party dependencies — the engine + isolation harness are pure Node.
COPY pine-engine.js worker.js runner.js server.js ./

USER node
EXPOSE 9871
CMD ["node", "server.js"]
