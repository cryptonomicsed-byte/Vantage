defmodule FeedSupervisor.Worker do
  @moduledoc """
  One supervised OS process, owned through an Erlang Port.

  States: `waiting` (start delay) -> `running` -> on exit `backoff` -> `running`
  ... ; `failed` after `max_restarts` restarts inside `restart_window_s`;
  `stopping` during shutdown. Backoff doubles from `initial_ms` up to `max_ms`
  and resets once a run has stayed up `reset_after_s`.

  The command runs under priv/run_child.sh, which puts it in its own process
  group and kills that group when the port's stdin closes, so a BEAM crash does
  not orphan it. Shutdown: SIGTERM to the wrapper (forwarded to the group), then
  SIGKILL to the group after `kill_timeout_ms`.
  """
  use GenServer
  require Logger

  @pid_marker "__FEED_CHILD_PID__ "

  def start_link(spec), do: GenServer.start_link(__MODULE__, spec, name: via(spec.name))

  def child_spec(spec) do
    %{
      id: {__MODULE__, spec.name},
      start: {__MODULE__, :start_link, [spec]},
      # long enough for terminate/2 to TERM, wait, then KILL
      shutdown: spec.kill_timeout_ms + 2_000
    }
  end

  def via(name), do: {:via, Registry, {FeedSupervisor.Registry, name}}

  def status(name_or_pid), do: GenServer.call(target(name_or_pid), :status)

  defp target(p) when is_pid(p), do: p
  defp target(name), do: via(name)

  # ── GenServer ──────────────────────────────────────────────────────────────

  @impl true
  def init(spec) do
    Process.flag(:trap_exit, true)
    Registry.update_value(FeedSupervisor.Registry, spec.name, fn _ -> System.monotonic_time() end)

    state = %{
      spec: spec,
      status: :waiting,
      port: nil,
      os_pid: nil,
      child_pgid: nil,
      started_at: nil,
      restarts: 0,
      restart_times: [],
      backoff_ms: spec.backoff.initial_ms,
      next_start_at: nil,
      last_delay_ms: nil,
      last_exit_status: nil,
      last_exit_at: nil,
      lines: :queue.new(),
      nlines: 0,
      partial: "",
      timer: nil
    }

    if spec.start_delay_ms > 0,
      do: Logger.info("[#{spec.name}] starting in #{spec.start_delay_ms} ms")

    {:ok, schedule_start(state, spec.start_delay_ms)}
  end

  @impl true
  def handle_info(:start, %{status: s} = state) when s in [:waiting, :backoff] do
    {:noreply, spawn_port(%{state | timer: nil})}
  end

  def handle_info(:start, state), do: {:noreply, state}

  def handle_info({port, {:data, {:eol, chunk}}}, %{port: port} = state) do
    line = state.partial <> chunk
    {:noreply, on_line(%{state | partial: ""}, line)}
  end

  def handle_info({port, {:data, {:noeol, chunk}}}, %{port: port} = state) do
    {:noreply, %{state | partial: state.partial <> chunk}}
  end

  def handle_info({port, {:exit_status, code}}, %{port: port} = state) do
    state = flush_partial(state)
    {:noreply, on_exit(state, code)}
  end

  # the port is linked to us; its EXIT can arrive after exit_status cleared it
  def handle_info({:EXIT, port, _reason}, state) when is_port(port), do: {:noreply, state}
  def handle_info({:EXIT, _from, reason}, state), do: {:stop, reason, state}
  def handle_info(_msg, state), do: {:noreply, state}

  @impl true
  def handle_call(:status, _from, state), do: {:reply, snapshot(state), state}

  @impl true
  def terminate(_reason, %{port: nil}), do: :ok

  def terminate(_reason, state) do
    name = state.spec.name
    Logger.info("[#{name}] stopping: SIGTERM to os pid #{state.os_pid}")
    signal(state.os_pid, "TERM")

    receive do
      {port, {:exit_status, code}} when port == state.port ->
        Logger.info("[#{name}] stopped, exit status #{code}")
    after
      state.spec.kill_timeout_ms ->
        Logger.warning("[#{name}] no exit after #{state.spec.kill_timeout_ms} ms: SIGKILL")
        if state.child_pgid, do: signal(-state.child_pgid, "KILL")
        signal(state.os_pid, "KILL")

        receive do
          {port, {:exit_status, _}} when port == state.port -> :ok
        after
          2_000 -> :ok
        end
    end

    catch_close(state.port)
    :ok
  end

  # ── internals ──────────────────────────────────────────────────────────────

  defp spawn_port(state) do
    spec = state.spec
    wrapper = Application.app_dir(:feed_supervisor, "priv/run_child.sh")

    opts =
      [
        :exit_status,
        :binary,
        :stderr_to_stdout,
        {:line, 4096},
        {:args, [wrapper, spec.cmd | spec.args]}
      ] ++
        if(spec.cwd, do: [{:cd, spec.cwd}], else: []) ++
        [
          {:env,
           Enum.map(spec.env, fn {k, v} -> {String.to_charlist(k), String.to_charlist(v)} end)}
        ]

    try do
      port = Port.open({:spawn_executable, "/bin/sh"}, opts)
      {:os_pid, os_pid} = Port.info(port, :os_pid)

      Logger.info(
        "[#{spec.name}] started os pid #{os_pid}: #{spec.cmd} #{Enum.join(spec.args, " ")}"
      )

      %{
        state
        | status: :running,
          port: port,
          os_pid: os_pid,
          child_pgid: nil,
          started_at: System.monotonic_time(:millisecond),
          partial: "",
          next_start_at: nil
      }
    rescue
      e ->
        Logger.error("[#{spec.name}] spawn failed: #{Exception.message(e)}")
        on_exit(%{state | started_at: System.monotonic_time(:millisecond)}, :spawn_failed)
    end
  end

  defp on_line(state, @pid_marker <> pid) do
    case Integer.parse(pid) do
      {n, _} when state.child_pgid == nil -> %{state | child_pgid: n}
      _ -> keep_line(state, @pid_marker <> pid)
    end
  end

  defp on_line(state, line), do: keep_line(state, line)

  defp keep_line(state, line) do
    Logger.info("[#{state.spec.name}] #{line}")
    q = :queue.in(line, state.lines)

    if state.nlines >= state.spec.lines_kept do
      {_, q} = :queue.out(q)
      %{state | lines: q}
    else
      %{state | lines: q, nlines: state.nlines + 1}
    end
  end

  defp flush_partial(%{partial: ""} = s), do: s
  defp flush_partial(s), do: on_line(%{s | partial: ""}, s.partial)

  defp on_exit(state, code) do
    spec = state.spec
    now = System.monotonic_time(:millisecond)
    up_ms = if state.started_at, do: now - state.started_at, else: 0
    # a run that stayed up long enough counts as healthy: back to initial backoff
    backoff =
      if up_ms >= spec.backoff.reset_after_s * 1000,
        do: spec.backoff.initial_ms,
        else: state.backoff_ms

    window_ms = spec.restart_window_s * 1000
    recent = Enum.filter(state.restart_times, &(now - &1 < window_ms))

    state = %{
      state
      | port: nil,
        os_pid: nil,
        child_pgid: nil,
        started_at: nil,
        last_exit_status: code,
        last_exit_at: now,
        restart_times: recent
    }

    if length(recent) >= spec.max_restarts do
      Logger.error(
        "[#{spec.name}] exited (#{inspect(code)}); #{length(recent)} restarts within " <>
          "#{spec.restart_window_s}s >= max_restarts #{spec.max_restarts}: failed"
      )

      %{state | status: :failed, backoff_ms: backoff}
    else
      Logger.warning(
        "[#{spec.name}] exited (#{inspect(code)}) after #{up_ms} ms; restart in #{backoff} ms"
      )

      next = min(round(backoff * spec.backoff.factor), spec.backoff.max_ms)

      %{
        state
        | status: :backoff,
          restarts: state.restarts + 1,
          restart_times: [now | recent],
          last_delay_ms: backoff,
          backoff_ms: max(next, 0)
      }
      |> schedule_start(backoff)
    end
  end

  defp schedule_start(state, delay) do
    ref = Process.send_after(self(), :start, delay)
    %{state | timer: ref, next_start_at: System.monotonic_time(:millisecond) + delay}
  end

  defp snapshot(state) do
    now = System.monotonic_time(:millisecond)

    %{
      name: state.spec.name,
      status: state.status,
      pid: state.os_pid,
      restarts: state.restarts,
      last_exit_status: state.last_exit_status,
      uptime_s:
        if(state.started_at, do: Float.round((now - state.started_at) / 1000, 1), else: nil),
      next_start_in_ms: if(state.next_start_at, do: max(state.next_start_at - now, 0), else: nil),
      backoff_ms: state.backoff_ms,
      last_restart_delay_ms: state.last_delay_ms,
      last_lines: :queue.to_list(state.lines)
    }
  end

  defp signal(nil, _sig), do: :ok

  defp signal(pid, sig) do
    System.cmd("kill", ["-#{sig}", "--", Integer.to_string(pid)], stderr_to_stdout: true)
    :ok
  end

  defp catch_close(port) do
    try do
      Port.close(port)
    rescue
      ArgumentError -> :ok
    end
  end
end
