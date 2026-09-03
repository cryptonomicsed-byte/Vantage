# The Conductor: turn arbitration and presence for guild workspace channels.
#
# No package fetch at build time -- the service has no dependencies beyond
# OTP itself (see mix.exs for why). That keeps the image small and the build
# reproducible offline.
FROM elixir:1.15-alpine AS build

ENV MIX_ENV=prod
WORKDIR /build

COPY mix.exs ./
RUN mix local.hex --force && mix local.rebar --force && mix deps.get

COPY lib ./lib
RUN mix compile && mix release --overwrite

FROM alpine:3.19

RUN apk add --no-cache libstdc++ ncurses-libs openssl \
    && addgroup -S conductor && adduser -S -G conductor conductor

WORKDIR /app
COPY --from=build /build/_build/prod/rel/conductor ./

# Same posture as ops/code-sandbox: non-root, nothing writable it doesn't
# need. The Conductor holds no keys and no database credentials, so a
# compromise here yields turn state and nothing else.
USER conductor

ENV CONDUCTOR_PORT=4500
EXPOSE 4500

# A container that cannot reach the backend is still useful (channels fall
# back to open flow), so the health check only asserts the service is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:${CONDUCTOR_PORT}/health || exit 1

CMD ["bin/conductor", "start"]
