import Config

config :logger, :console, format: "$time [$level] $message\n"

# Workers come from FEED_SUPERVISOR_CONFIG (default: feeds.json in the cwd);
# HTTP health listens on 127.0.0.1:FEED_SUPERVISOR_PORT (default 8094).
config :feed_supervisor, autostart: true, http: true

if config_env() == :test do
  # Tests start their own workers and HTTP listener on an ephemeral port.
  config :feed_supervisor, autostart: false, http: false
  config :logger, level: :warning
end
