defmodule Conductor.ChannelServerTest do
  @moduledoc """
  The process layer: effects reaching sockets, structure being honoured, and
  the crash/disconnect behaviour that is the reason this tier runs on the
  BEAM at all.
  """
  use ExUnit.Case, async: false

  alias Conductor.ChannelServer

  defmodule StubBackend do
    @moduledoc false
    # Records what the ChannelServer asked the backend to do, so the tests
    # can assert that system events and violations are actually handed off
    # for signing rather than invented locally.
    def channel_structure(channel_id, _opts \\ []) do
      case :persistent_term.get({__MODULE__, :structure}, nil) do
        nil -> {:error, :not_configured}
        structure -> {:ok, Map.put(structure, "channel_id", channel_id)}
      end
    end

    def publish_system(channel_id, text) do
      send(owner(), {:backend, :system, channel_id, text})
      :ok
    end

    def report_violation(channel_id, principal_id, reason) do
      send(owner(), {:backend, :violation, channel_id, principal_id, reason})
      :ok
    end

    defp owner, do: :persistent_term.get({__MODULE__, :owner})
  end

  setup do
    :persistent_term.put({StubBackend, :owner}, self())
    Application.put_env(:conductor, :backend, StubBackend)

    on_exit(fn ->
      Application.delete_env(:conductor, :backend)
      :persistent_term.erase({StubBackend, :structure})
    end)

    :ok
  end

  defp start_channel(structure) do
    :persistent_term.put({StubBackend, :structure}, structure)
    channel_id = System.unique_integer([:positive])
    {:ok, pid} = ChannelServer.start_link(channel_id)
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid, :normal) end)
    {channel_id, pid}
  end

  describe "structure" do
    test "flow mode comes from the backend" do
      {channel_id, _} = start_channel(%{"flow_mode" => "round_robin"})
      assert {:ok, %{flow_mode: "round_robin"}} = ChannelServer.snapshot(channel_id)
    end

    test "an unreachable backend falls back to open rather than locking up" do
      :persistent_term.erase({StubBackend, :structure})
      channel_id = System.unique_integer([:positive])
      {:ok, pid} = ChannelServer.start_link(channel_id)
      on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid, :normal) end)

      assert {:ok, %{flow_mode: "open"}} = ChannelServer.snapshot(channel_id)
    end
  end

  describe "effects reach the socket" do
    test "a grant is pushed to the principal that asked" do
      {channel_id, _} = start_channel(%{"flow_mode" => "round_robin"})
      ChannelServer.join(channel_id, 1, %{name: "one"})
      ChannelServer.request_floor(channel_id, 1)

      assert_receive {:conductor, %{type: "grant", principal_id: 1}}
    end

    test "presence is broadcast to everyone attached" do
      {channel_id, _} = start_channel(%{"flow_mode" => "open"})
      ChannelServer.join(channel_id, 1, %{name: "one"})
      # A second principal on a socket we own, so we see the broadcast.
      ChannelServer.join(channel_id, 2, %{name: "two"}, self())

      assert_receive {:conductor, %{type: "presence", event: "joined", principal_id: 2}}
    end

    test "a queued principal is told its position, not just refused" do
      {channel_id, _} = start_channel(%{"flow_mode" => "round_robin"})
      ChannelServer.join(channel_id, 1, %{})
      ChannelServer.join(channel_id, 2, %{}, self())
      ChannelServer.request_floor(channel_id, 1)
      ChannelServer.request_floor(channel_id, 2)

      assert_receive {:conductor, %{type: "queued", position: 1}}
    end
  end

  describe "handing work to the backend" do
    test "floor grants are sent for signing, not written locally" do
      # The Conductor holds no key; a grant only becomes part of the
      # transcript because the backend signs and publishes it.
      {channel_id, _} = start_channel(%{"flow_mode" => "round_robin"})
      ChannelServer.join(channel_id, 1, %{})
      ChannelServer.request_floor(channel_id, 1)

      assert_receive {:backend, :system, ^channel_id, text}
      assert text =~ "floor granted to 1"
    end

    test "an out-of-turn post is reported, and the message is not blocked" do
      {channel_id, _} = start_channel(%{"flow_mode" => "round_robin"})
      ChannelServer.join(channel_id, 1, %{})
      ChannelServer.join(channel_id, 2, %{}, self())
      ChannelServer.request_floor(channel_id, 1)

      # Principal 2 published anyway; the relay already accepted it.
      assert :ok = ChannelServer.observed(channel_id, 2, "say")

      assert_receive {:backend, :violation, ^channel_id, 2, reason}
      assert reason =~ "without the floor"
      # And the floor stayed where it belonged.
      assert {:ok, %{floor: 1}} = ChannelServer.snapshot(channel_id)
    end
  end

  describe "disconnects" do
    test "a dead socket releases the floor without waiting for a timeout" do
      {channel_id, _} = start_channel(%{"flow_mode" => "round_robin"})

      holder = spawn(fn -> receive do: (:never -> :ok) end)
      ChannelServer.join(channel_id, 1, %{}, holder)
      ChannelServer.join(channel_id, 2, %{}, self())
      ChannelServer.request_floor(channel_id, 1)
      ChannelServer.request_floor(channel_id, 2)
      assert {:ok, %{floor: 1}} = ChannelServer.snapshot(channel_id)

      Process.exit(holder, :kill)

      # The DOWN monitor should hand the floor straight to the queue.
      assert_receive {:conductor, %{type: "grant", principal_id: 2}}, 1_000
      assert {:ok, %{floor: 2}} = ChannelServer.snapshot(channel_id)
    end
  end

  describe "restart" do
    test "a crashed channel comes back with structure re-read and no stale floor" do
      # The property that justifies this tier: state here is losable.
      {channel_id, pid} = start_channel(%{"flow_mode" => "round_robin"})
      ChannelServer.join(channel_id, 1, %{})
      ChannelServer.request_floor(channel_id, 1)
      assert {:ok, %{floor: 1}} = ChannelServer.snapshot(channel_id)

      Process.flag(:trap_exit, true)
      Process.exit(pid, :kill)
      assert_receive {:EXIT, ^pid, :killed}

      {:ok, new_pid} = ChannelServer.start_link(channel_id)
      on_exit(fn -> if Process.alive?(new_pid), do: GenServer.stop(new_pid, :normal) end)

      assert {:ok, %{floor: nil, flow_mode: "round_robin", present: []}} =
               ChannelServer.snapshot(channel_id)
    end
  end

  describe "unknown channels" do
    test "calls to a channel that is not running are refused cleanly" do
      assert {:error, :no_channel} = ChannelServer.snapshot(999_999_999)
    end
  end
end
