defmodule FeedSupervisorTest do
  use ExUnit.Case, async: false

  alias FeedSupervisor.{Config, Health, Worker, Workers}

  @moduletag timeout: 60_000

  setup do
    dir = Path.join(System.tmp_dir!(), "feedsup-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    {:ok, dir: dir}
  end

  defp spec(name, script, extra \\ %{}) do
    Map.merge(
      %{
        "name" => name,
        "cmd" => "sh",
        "args" => ["-c", script],
        "backoff" => %{"initial_ms" => 50, "max_ms" => 1_000},
        "kill_timeout_ms" => 2_000
      },
      extra
    )
    |> Config.worker!()
  end

  defp start!(spec) do
    {:ok, pid} = Workers.start_worker(spec)
    on_exit(fn -> Workers.stop_worker(spec.name) end)
    pid
  end

  defp wait_until(fun, timeout \\ 10_000) do
    deadline = System.monotonic_time(:millisecond) + timeout
    do_wait(fun, deadline)
  end

  defp do_wait(fun, deadline) do
    case fun.() do
      v when v not in [nil, false] ->
        v

      _ ->
        if System.monotonic_time(:millisecond) > deadline, do: flunk("condition not met in time")
        Process.sleep(20)
        do_wait(fun, deadline)
    end
  end

  defp alive?(os_pid),
    do: match?({_, 0}, System.cmd("kill", ["-0", "#{os_pid}"], stderr_to_stdout: true))

  defp stamps(file) do
    case File.read(file) do
      {:ok, s} -> s |> String.split("\n", trim: true) |> Enum.map(&String.to_integer/1)
      _ -> []
    end
  end

  defp gaps_ms(ns),
    do: ns |> Enum.chunk_every(2, 1, :discard) |> Enum.map(fn [a, b] -> div(b - a, 1_000_000) end)

  # ── config ───────────────────────────────────────────────────────────────

  test "config: defaults, PATH lookup, validation, shipped feeds.json parses" do
    s = Config.worker!(%{"name" => "x", "cmd" => "sh"})
    assert s.cmd == System.find_executable("sh")
    assert s.backoff == %{initial_ms: 1_000, max_ms: 60_000, factor: 2, reset_after_s: 120}
    assert s.start_delay_ms == 0 and s.max_restarts == 10

    assert_raise ArgumentError, fn -> Config.worker!(%{"cmd" => "sh"}) end

    assert_raise ArgumentError, fn ->
      Config.worker!(%{"name" => "x", "cmd" => "no-such-binary-xyz"})
    end

    assert_raise ArgumentError, fn ->
      Config.worker!(%{"name" => "x", "cmd" => "sh", "max_restarts" => -1})
    end

    assert_raise ArgumentError, fn ->
      Config.parse!(%{
        "workers" => [%{"name" => "a", "cmd" => "sh"}, %{"name" => "a", "cmd" => "sh"}]
      })
    end

    [curve] = Config.load!(Path.expand("../feeds.json", __DIR__))
    assert curve.name == "curve_listener"
    assert curve.args == ["-m", "backend.curve_listener"]
    assert curve.env["CURVE_LISTENER_ENABLED"] == "1"
  end

  # ── lifecycle ────────────────────────────────────────────────────────────

  test "start: runs the command, captures output lines, reports os pid" do
    start!(spec("t_start", "echo hello; echo world >&2; exec sleep 30"))

    st =
      wait_until(fn ->
        s = Worker.status("t_start")
        s.status == :running and length(s.last_lines) == 2 and s
      end)

    assert is_integer(st.pid) and alive?(st.pid)
    assert st.last_lines == ["hello", "world"]
    assert st.restarts == 0 and st.last_exit_status == nil and is_number(st.uptime_s)
  end

  # Distinct restart delays in the order the worker used them.
  defp delays(name, n) do
    Stream.repeatedly(fn ->
      Process.sleep(10)
      Worker.status(name).last_restart_delay_ms
    end)
    |> Stream.reject(&is_nil/1)
    |> Stream.dedup()
    |> Enum.take(n)
  end

  test "restart with exponential backoff, capped", %{dir: dir} do
    f = Path.join(dir, "runs")

    start!(
      spec("t_backoff", "date +%s%N >> #{f}; exit 3", %{
        "max_restarts" => 100,
        "backoff" => %{"initial_ms" => 100, "max_ms" => 400, "factor" => 2}
      })
    )

    # 100 -> 200 -> 400 (cap); the cap repeats, so dedup stops at 3 values
    assert delays("t_backoff", 3) == [100, 200, 400]
    wait_until(fn -> length(stamps(f)) >= 5 end)
    [g1, g2, g3, g4 | _] = gaps_ms(stamps(f))
    # wall-clock lower bounds only: a loaded host can only make gaps longer
    assert g1 >= 95 and g2 >= 195 and g3 >= 395 and g4 >= 395, inspect([g1, g2, g3, g4])

    st = Worker.status("t_backoff")
    assert st.restarts >= 4 and st.last_exit_status == 3 and st.last_restart_delay_ms == 400
    assert st.backoff_ms == 400
  end

  test "backoff resets after a run that stayed healthy" do
    start!(
      spec("t_reset", "sleep 0.3; exit 1", %{
        "max_restarts" => 100,
        "backoff" => %{
          "initial_ms" => 100,
          "max_ms" => 10_000,
          "factor" => 4,
          "reset_after_s" => 0.2
        }
      })
    )

    wait_until(fn -> Worker.status("t_reset").restarts >= 3 end)
    # without the reset the delays would be 100, 400, 1600
    assert Worker.status("t_reset").last_restart_delay_ms == 100
  end

  test "backoff does not reset for runs shorter than reset_after_s" do
    start!(
      spec("t_noreset", "exit 1", %{
        "max_restarts" => 100,
        "backoff" => %{
          "initial_ms" => 50,
          "max_ms" => 10_000,
          "factor" => 2,
          "reset_after_s" => 60
        }
      })
    )

    assert delays("t_noreset", 3) == [50, 100, 200]
  end

  test "failed after max_restarts in the window; health reports degraded" do
    start!(
      spec("t_fail", "echo nope; exit 1", %{
        "max_restarts" => 3,
        "restart_window_s" => 60,
        "backoff" => %{"initial_ms" => 20}
      })
    )

    st =
      wait_until(fn ->
        s = Worker.status("t_fail")
        s.status == :failed and s
      end)

    assert st.restarts == 3 and st.last_exit_status == 1 and st.pid == nil
    Process.sleep(200)
    assert Worker.status("t_fail").restarts == 3, "kept restarting after failed"

    h = Health.snapshot()
    assert h.status == "degraded" and h.workers["t_fail"].status == :failed
  end

  test "staggered start honours start_delay_ms", %{dir: dir} do
    f = Path.join(dir, "order")
    run = fn n -> "echo #{n} >> #{f}; exec sleep 30" end
    start!(spec("t_c", run.("c"), %{"start_delay_ms" => 600}))
    start!(spec("t_a", run.("a"), %{"start_delay_ms" => 0}))
    start!(spec("t_b", run.("b"), %{"start_delay_ms" => 300}))

    assert Worker.status("t_c").status == :waiting
    assert Worker.status("t_c").next_start_in_ms > 300

    wait_until(fn -> match?({:ok, s} when byte_size(s) >= 6, File.read(f)) end)
    assert File.read!(f) |> String.split() == ["a", "b", "c"]

    assert Health.snapshot().order |> Enum.filter(&(&1 in ["t_a", "t_b", "t_c"])) == [
             "t_c",
             "t_a",
             "t_b"
           ]
  end

  # ── shutdown / orphans ───────────────────────────────────────────────────

  defp pid_line(name, tag) do
    wait_until(fn ->
      Enum.find_value(Worker.status(name).last_lines, fn l ->
        case String.split(l) do
          [^tag, p] -> String.to_integer(p)
          _ -> nil
        end
      end)
    end)
  end

  test "graceful stop SIGTERMs the child and its children" do
    start!(spec("t_term", "sleep 30 & echo GC $!; wait"))
    gc = pid_line("t_term", "GC")
    wrapper = Worker.status("t_term").pid
    assert alive?(gc) and alive?(wrapper)

    {us, _} = :timer.tc(fn -> Workers.stop_worker("t_term") end)
    assert us < 1_500_000, "TERM path should not wait for the kill timeout"
    wait_until(fn -> not alive?(gc) and not alive?(wrapper) end, 3_000)
  end

  test "child ignoring SIGTERM is SIGKILLed after kill_timeout_ms" do
    start!(
      spec("t_kill", ~s(trap "" TERM; echo PID $$; while :; do sleep 0.05; done), %{
        "kill_timeout_ms" => 300
      })
    )

    child = pid_line("t_kill", "PID")
    {us, _} = :timer.tc(fn -> Workers.stop_worker("t_kill") end)
    assert us >= 300_000
    wait_until(fn -> not alive?(child) end, 3_000)
  end

  test "port closed without a stop (BEAM death) does not orphan the child" do
    wrapper = Application.app_dir(:feed_supervisor, "priv/run_child.sh")

    port =
      Port.open(
        {:spawn_executable, "/bin/sh"},
        [
          :binary,
          :exit_status,
          :stderr_to_stdout,
          {:line, 4096},
          {:args, [wrapper, "/bin/sh", "-c", "echo PID $$; exec sleep 30"]}
        ]
      )

    child = recv_pid(port)
    assert alive?(child)
    Port.close(port)
    wait_until(fn -> not alive?(child) end, 5_000)
  end

  defp recv_pid(port) do
    receive do
      {^port, {:data, {:eol, "PID " <> p}}} -> String.to_integer(p)
      {^port, {:data, _}} -> recv_pid(port)
    after
      5_000 -> flunk("no PID line")
    end
  end

  # ── HTTP ─────────────────────────────────────────────────────────────────

  test "GET /health returns per-worker JSON" do
    start_supervised!(FeedSupervisor.HTTP.child_spec(port: 0, ref: :feedsup_test_http))
    port = :ranch.get_port(:feedsup_test_http)
    start!(spec("t_http", "echo up; exec sleep 30"))
    wait_until(fn -> Worker.status("t_http").last_lines == ["up"] end)

    {:ok, {{_, 200, _}, headers, body}} =
      :httpc.request(:get, {~c"http://127.0.0.1:#{port}/health", []}, [], body_format: :binary)

    assert {~c"content-type", ~c"application/json" ++ _} =
             List.keyfind(headers, ~c"content-type", 0)

    j = Jason.decode!(body)
    w = j["workers"]["t_http"]
    assert w["status"] == "running" and is_integer(w["pid"]) and w["restarts"] == 0

    assert Map.has_key?(w, "last_exit_status") and is_number(w["uptime_s"]) and
             w["last_lines"] == ["up"]

    assert j["status"] in ["ok", "degraded"]

    {:ok, {{_, 200, _}, _, one}} =
      :httpc.request(:get, {~c"http://127.0.0.1:#{port}/health/t_http", []}, [],
        body_format: :binary
      )

    assert Jason.decode!(one)["status"] == "running"

    {:ok, {{_, 404, _}, _, _}} =
      :httpc.request(:get, {~c"http://127.0.0.1:#{port}/health/nope", []}, [], [])
  end
end
