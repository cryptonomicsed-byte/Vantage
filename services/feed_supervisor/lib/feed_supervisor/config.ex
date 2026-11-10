defmodule FeedSupervisor.Config do
  @moduledoc """
  Worker specs from a JSON file:

      {"workers": [{"name": "...", "cmd": "...", "args": [], "cwd": "...",
                    "env": {"K": "V"}, "start_delay_ms": 0, "max_restarts": 10,
                    "restart_window_s": 600, "kill_timeout_ms": 10000,
                    "backoff": {"initial_ms": 1000, "max_ms": 60000,
                                "factor": 2, "reset_after_s": 120}}]}
  """

  @defaults %{
    args: [],
    cwd: nil,
    env: %{},
    start_delay_ms: 0,
    max_restarts: 10,
    restart_window_s: 600,
    kill_timeout_ms: 10_000,
    lines_kept: 20,
    backoff: %{initial_ms: 1_000, max_ms: 60_000, factor: 2, reset_after_s: 120}
  }

  def path do
    System.get_env("FEED_SUPERVISOR_CONFIG") ||
      Application.get_env(:feed_supervisor, :config_path, "feeds.json")
  end

  def load!(path \\ path()) do
    path |> File.read!() |> Jason.decode!() |> parse!()
  end

  def parse!(%{"workers" => workers}) when is_list(workers) do
    specs = Enum.map(workers, &worker!/1)
    names = Enum.map(specs, & &1.name)

    if length(Enum.uniq(names)) != length(names),
      do: raise(ArgumentError, "duplicate worker names: #{inspect(names)}")

    specs
  end

  def parse!(other),
    do: raise(ArgumentError, "expected {\"workers\": [...]}, got #{inspect(other)}")

  def worker!(m) when is_map(m) do
    name = fetch!(m, "name")
    cmd = fetch!(m, "cmd")
    b = Map.get(m, "backoff", %{})
    db = @defaults.backoff

    %{
      name: name,
      cmd: resolve_cmd!(cmd),
      args: Enum.map(Map.get(m, "args", @defaults.args), &to_string/1),
      cwd: Map.get(m, "cwd", @defaults.cwd),
      env:
        Map.new(Map.get(m, "env", @defaults.env), fn {k, v} -> {to_string(k), to_string(v)} end),
      start_delay_ms: int(m, "start_delay_ms", @defaults.start_delay_ms),
      max_restarts: int(m, "max_restarts", @defaults.max_restarts),
      restart_window_s: num(m, "restart_window_s", @defaults.restart_window_s),
      kill_timeout_ms: int(m, "kill_timeout_ms", @defaults.kill_timeout_ms),
      lines_kept: int(m, "lines_kept", @defaults.lines_kept),
      backoff: %{
        initial_ms: int(b, "initial_ms", db.initial_ms),
        max_ms: int(b, "max_ms", db.max_ms),
        factor: num(b, "factor", db.factor),
        reset_after_s: num(b, "reset_after_s", db.reset_after_s)
      }
    }
  end

  defp fetch!(m, k) do
    case Map.get(m, k) do
      v when is_binary(v) and v != "" -> v
      _ -> raise ArgumentError, "worker needs a non-empty \"#{k}\": #{inspect(m)}"
    end
  end

  defp int(m, k, d) do
    case Map.get(m, k, d) do
      v when is_integer(v) and v >= 0 -> v
      v -> raise ArgumentError, "#{k} must be a non-negative integer, got #{inspect(v)}"
    end
  end

  defp num(m, k, d) do
    case Map.get(m, k, d) do
      v when is_number(v) and v >= 0 -> v
      v -> raise ArgumentError, "#{k} must be a non-negative number, got #{inspect(v)}"
    end
  end

  # Absolute paths are used as given; bare names are looked up on PATH.
  defp resolve_cmd!("/" <> _ = abs), do: abs

  defp resolve_cmd!(cmd) do
    System.find_executable(cmd) || raise ArgumentError, "executable not found on PATH: #{cmd}"
  end
end
