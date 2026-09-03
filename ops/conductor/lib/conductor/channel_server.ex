defmodule Conductor.ChannelServer do
  @moduledoc """
  One supervised process per active channel.

  Holds `Conductor.Flow` plus the sockets currently attached, turns the
  state machine's effects into messages and backend calls, and expires
  floors on a timer.

  Everything in here is losable. If this process crashes, the supervisor
  restarts it, it re-reads the channel's structure from the backend, and
  clients reconnect — no message is lost, because no message was ever
  stored here. That property is the reason this tier exists at all.
  """
  use GenServer, restart: :transient
  require Logger

  alias Conductor.Flow

  # The backend is looked up rather than called directly so a test (or a
  # future in-process deployment) can supply its own. Defaults to the real
  # HTTP client; nothing in production sets this.
  defp backend, do: Application.get_env(:conductor, :backend, Conductor.Backend)

  # Floors are checked at this cadence rather than with a per-grant timer:
  # one predictable tick is easier to reason about than a timer per floor,
  # and a second of slack on a 90s turn costs nothing.
  @tick_ms 1_000

  # A channel with nobody in it stops, so idle channels cost nothing. It
  # starts again on the next join.
  @idle_shutdown_ms 120_000

  defmodule State do
    @moduledoc false
    defstruct [:channel_id, :flow, :sockets, :last_active]
  end

  # ── api ────────────────────────────────────────────────────────────────────

  def start_link(channel_id) do
    GenServer.start_link(__MODULE__, channel_id, name: via(channel_id))
  end

  def via(channel_id), do: {:via, Registry, {Conductor.Registry, channel_id}}

  @doc "Attach a socket as `principal_id`. The caller is monitored."
  def join(channel_id, principal_id, meta, socket \\ self()) do
    call(channel_id, {:join, principal_id, meta, socket})
  end

  def leave(channel_id, principal_id), do: call(channel_id, {:leave, principal_id})
  def request_floor(channel_id, principal_id), do: call(channel_id, {:request_floor, principal_id})
  def handoff(channel_id, from, to), do: call(channel_id, {:handoff, from, to})

  @doc "Vantage's indexer saw a message land on the relay."
  def observed(channel_id, principal_id, msg_type) do
    call(channel_id, {:observed, principal_id, msg_type})
  end

  def snapshot(channel_id), do: call(channel_id, :snapshot)

  defp call(channel_id, message) do
    GenServer.call(via(channel_id), message)
  catch
    :exit, {:noproc, _} -> {:error, :no_channel}
    :exit, {:timeout, _} -> {:error, :timeout}
  end

  # ── lifecycle ──────────────────────────────────────────────────────────────

  @impl true
  def init(channel_id) do
    Process.send_after(self(), :tick, @tick_ms)

    {:ok,
     %State{
       channel_id: channel_id,
       flow: build_flow(channel_id),
       sockets: %{},
       last_active: now_ms()
     }}
  end

  defp build_flow(channel_id) do
    case backend().channel_structure(channel_id) do
      {:ok, structure} ->
        Flow.new(
          mode: parse_mode(Map.get(structure, "flow_mode")),
          staff: Map.get(structure, "staff", []),
          floor_ttl_ms: Map.get(structure, "floor_ttl_ms", 90_000)
        )

      {:error, reason} ->
        # Unreachable backend must not mean an unusable channel. `:open` is
        # the safe default: it grants freely rather than withholding the
        # floor from everyone until the backend returns.
        Logger.warning("channel structure unavailable (#{inspect(reason)}); defaulting to open")
        Flow.new(mode: :open)
    end
  end

  defp parse_mode("round_robin"), do: :round_robin
  defp parse_mode("moderated"), do: :moderated
  defp parse_mode(_), do: :open

  # ── calls ──────────────────────────────────────────────────────────────────

  @impl true
  def handle_call({:join, principal_id, meta, socket}, _from, state) do
    Process.monitor(socket)
    sockets = Map.put(state.sockets, principal_id, socket)
    {flow, effects} = Flow.join(state.flow, principal_id, meta, now_ms())
    state = %{state | flow: flow, sockets: sockets, last_active: now_ms()}
    {:reply, {:ok, snapshot_of(state)}, dispatch(state, effects)}
  end

  def handle_call({:leave, principal_id}, _from, state) do
    {:reply, :ok, do_leave(state, principal_id)}
  end

  def handle_call({:request_floor, principal_id}, _from, state) do
    {flow, effects} = Flow.request_floor(state.flow, principal_id, now_ms())
    state = dispatch(%{state | flow: flow, last_active: now_ms()}, effects)
    {:reply, {:ok, snapshot_of(state)}, state}
  end

  def handle_call({:handoff, from, to}, _from, state) do
    {flow, effects} = Flow.handoff(state.flow, from, to, now_ms())
    state = dispatch(%{state | flow: flow, last_active: now_ms()}, effects)
    {:reply, {:ok, snapshot_of(state)}, state}
  end

  def handle_call({:observed, principal_id, msg_type}, _from, state) do
    {flow, effects} = Flow.observed(state.flow, principal_id, msg_type, now_ms())
    state = dispatch(%{state | flow: flow, last_active: now_ms()}, effects)
    {:reply, :ok, state}
  end

  def handle_call(:snapshot, _from, state) do
    {:reply, {:ok, snapshot_of(state)}, state}
  end

  @impl true
  def handle_info(:tick, state) do
    Process.send_after(self(), :tick, @tick_ms)
    {flow, effects} = Flow.tick(state.flow, now_ms())
    state = dispatch(%{state | flow: flow}, effects)

    if map_size(state.sockets) == 0 and now_ms() - state.last_active > @idle_shutdown_ms do
      {:stop, :normal, state}
    else
      {:noreply, state}
    end
  end

  # A socket died. Its principal has left, whether or not it said so.
  def handle_info({:DOWN, _ref, :process, pid, _reason}, state) do
    case Enum.find(state.sockets, fn {_id, socket} -> socket == pid end) do
      {principal_id, _} -> {:noreply, do_leave(state, principal_id)}
      nil -> {:noreply, state}
    end
  end

  def handle_info(_message, state), do: {:noreply, state}

  defp do_leave(state, principal_id) do
    {flow, effects} = Flow.leave(state.flow, principal_id, now_ms())
    sockets = Map.delete(state.sockets, principal_id)
    dispatch(%{state | flow: flow, sockets: sockets, last_active: now_ms()}, effects)
  end

  # ── effects ────────────────────────────────────────────────────────────────

  defp dispatch(state, effects), do: Enum.reduce(effects, state, &apply_effect(&2, &1))

  defp apply_effect(state, {:grant, principal_id, expires_at}) do
    to_one(state, principal_id, %{
      type: "grant",
      channel_id: state.channel_id,
      principal_id: principal_id,
      expires_at: expires_at
    })
  end

  defp apply_effect(state, {:queued, principal_id, position}) do
    to_one(state, principal_id, %{
      type: "queued",
      channel_id: state.channel_id,
      position: position
    })
  end

  defp apply_effect(state, {:rate_limited, principal_id, retry_after_ms}) do
    to_one(state, principal_id, %{
      type: "rate_limited",
      channel_id: state.channel_id,
      retry_after_ms: retry_after_ms
    })
  end

  defp apply_effect(state, {:presence, event, principal_id}) do
    broadcast(state, %{
      type: "presence",
      channel_id: state.channel_id,
      event: Atom.to_string(event),
      principal_id: principal_id
    })
  end

  defp apply_effect(state, {:violation, principal_id, reason}) do
    # Recorded, not blocked. The message is already on the relay.
    backend().report_violation(state.channel_id, principal_id, reason)

    broadcast(state, %{
      type: "violation",
      channel_id: state.channel_id,
      principal_id: principal_id,
      reason: reason
    })
  end

  defp apply_effect(state, {:system, text}) do
    # Into the durable transcript, via the backend that holds the key.
    backend().publish_system(state.channel_id, text)
    broadcast(state, %{type: "system", channel_id: state.channel_id, text: text})
  end

  defp to_one(state, principal_id, payload) do
    case Map.get(state.sockets, principal_id) do
      nil -> state
      socket -> send(socket, {:conductor, payload}) && state
    end
  end

  defp broadcast(state, payload) do
    Enum.each(state.sockets, fn {_id, socket} -> send(socket, {:conductor, payload}) end)
    state
  end

  defp snapshot_of(state) do
    %{
      channel_id: state.channel_id,
      flow_mode: Atom.to_string(state.flow.mode),
      floor: Flow.floor_holder(state.flow),
      queue: Flow.queue(state.flow),
      present: Map.keys(state.flow.presence)
    }
  end

  defp now_ms, do: System.system_time(:millisecond)
end
