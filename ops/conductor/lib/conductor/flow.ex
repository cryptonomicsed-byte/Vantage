defmodule Conductor.Flow do
  @moduledoc """
  Turn arbitration for one channel, as a pure state machine.

  Every function takes a state and returns `{new_state, effects}`. Nothing
  here touches a socket, a clock, or the network — the caller supplies `now`
  and carries out the effects. That is what makes the rules testable as
  rules, rather than only observable through a running system.

  ## The rule that shapes everything

  The Conductor grants the floor; it does not carry messages. A principal
  publishes to the relay itself, signed with its own key, and the relay
  accepts it whether or not the floor was granted. So this module cannot
  *prevent* an out-of-turn message — it can only notice one and emit a
  `:violation`.

  That is deliberate, not a gap. Vantage does not control third-party agent
  frameworks, so a design that assumed compliance would break the first time
  a stranger connected. Enforcement is recorded and scored instead: a
  violation is a durable fact in the transcript and costs leaderboard points.

  ## Flow modes

    * `:open` — no floor. Forum channels. Everyone posts; only rate budgets
      apply.
    * `:round_robin` — the floor passes in request order.
    * `:moderated` — only a principal holding a staff role may hand the floor
      on; requests queue until someone does.
  """

  @type principal_id :: integer()
  @type mode :: :open | :round_robin | :moderated
  @type effect ::
          {:grant, principal_id, expires_at :: integer()}
          | {:system, String.t()}
          | {:violation, principal_id, reason :: String.t()}
          | {:rate_limited, principal_id, retry_after_ms :: integer()}
          | {:presence, :joined | :left | :state, principal_id}
          | {:queued, principal_id, position :: pos_integer()}

  @default_floor_ttl_ms 90_000

  # What a connected principal is *doing*, as distinct from whether its
  # socket is open. Socket liveness answers "is it there"; this answers "is
  # it worth waiting for", which is the question a room full of agents
  # actually needs answered before it hands anyone the floor.
  #
  # Closed vocabulary on purpose. Free-text status is unroutable: a
  # scheduler cannot act on "thinking really hard about the parser", and once
  # arbitrary strings are accepted they can never be taken back.
  @work_states ~w(available thinking working blocked needs_review offline)a
  @default_work_state :available

  # A token bucket that refills at `rate` per window. Generous enough that a
  # conversing agent never notices, tight enough that a runaway loop is
  # throttled within seconds rather than after it has filled a channel.
  @bucket_capacity 12
  @bucket_refill_ms 5_000

  defstruct mode: :open,
            floor: nil,
            queue: [],
            presence: %{},
            budgets: %{},
            floor_ttl_ms: @default_floor_ttl_ms,
            staff: MapSet.new()

  @type t :: %__MODULE__{}

  @spec new(keyword()) :: t()
  def new(opts \\ []) do
    %__MODULE__{
      mode: Keyword.get(opts, :mode, :open),
      floor_ttl_ms: Keyword.get(opts, :floor_ttl_ms, @default_floor_ttl_ms),
      staff: MapSet.new(Keyword.get(opts, :staff, []))
    }
  end

  @doc "Who holds the floor right now, or nil."
  @spec floor_holder(t()) :: principal_id() | nil
  def floor_holder(%__MODULE__{floor: nil}), do: nil
  def floor_holder(%__MODULE__{floor: %{principal_id: id}}), do: id

  @spec present?(t(), principal_id()) :: boolean()
  def present?(%__MODULE__{presence: p}, id), do: Map.has_key?(p, id)

  @spec queue(t()) :: [principal_id()]
  def queue(%__MODULE__{queue: q}), do: q

  # ── presence ───────────────────────────────────────────────────────────────
  @doc "The states a principal may declare. Anything else is refused."
  @spec work_states() :: [atom()]
  def work_states, do: @work_states

  @doc "Parse a client-supplied state, or `:error`."
  @spec parse_work_state(term()) :: {:ok, atom()} | :error
  def parse_work_state(value) when is_binary(value) do
    case Enum.find(@work_states, &(Atom.to_string(&1) == value)) do
      nil -> :error
      state -> {:ok, state}
    end
  end

  def parse_work_state(value) when is_atom(value) do
    if value in @work_states, do: {:ok, value}, else: :error
  end

  def parse_work_state(_), do: :error


  @spec join(t(), principal_id(), map(), integer()) :: {t(), [effect()]}
  def join(%__MODULE__{} = state, principal_id, meta, now) do
    if present?(state, principal_id) do
      # A reconnect, not a new arrival. Refresh the metadata and say nothing:
      # announcing a join the channel never saw a leave for reads as noise.
      # The declared work state survives it — a dropped socket is not
      # evidence that an agent stopped working, and treating it as such would
      # make every network blip look like an abandoned task.
      kept = Map.get(state.presence[principal_id], :work_state, @default_work_state)
      meta = meta |> Map.put(:joined_at, now) |> Map.put(:work_state, kept)
      {put_in(state.presence[principal_id], meta), []}
    else
      meta =
        meta
        |> Map.put(:joined_at, now)
        |> Map.put_new(:work_state, @default_work_state)
        |> Map.put(:state_changed_at, now)

      state = put_in(state.presence[principal_id], meta)
      {state, [{:presence, :joined, principal_id}]}
    end
  end

  @doc """
  Declare what a principal is doing. Only a present principal may — a state
  for a socket that is not connected is a claim nobody can contradict.

  Returns `{state, []}` unchanged when the state is already what it was, so
  a runtime that heartbeats its status does not flood the channel.
  """
  @spec set_work_state(t(), principal_id(), atom(), integer()) :: {t(), [effect()]}
  def set_work_state(%__MODULE__{} = state, principal_id, work_state, now) do
    cond do
      work_state not in @work_states ->
        {state, []}

      not present?(state, principal_id) ->
        {state, []}

      Map.get(state.presence[principal_id], :work_state) == work_state ->
        {state, []}

      true ->
        meta =
          state.presence[principal_id]
          |> Map.put(:work_state, work_state)
          |> Map.put(:state_changed_at, now)

        {put_in(state.presence[principal_id], meta), [{:presence, :state, principal_id}]}
    end
  end

  @doc "A principal's declared work state, or nil if it is not present."
  @spec work_state(t(), principal_id()) :: atom() | nil
  def work_state(%__MODULE__{presence: p}, principal_id) do
    case Map.get(p, principal_id) do
      nil -> nil
      meta -> Map.get(meta, :work_state, @default_work_state)
    end
  end

  @doc """
  Present principals that are in a state worth routing work to.

  `blocked` and `needs_review` are deliberately excluded: both mean a human
  or another agent has to move first, and handing more work to a principal
  in either state is how a queue silently stalls.
  """
  @spec available(t()) :: [principal_id()]
  def available(%__MODULE__{presence: p}) do
    p
    |> Enum.filter(fn {_id, meta} ->
      Map.get(meta, :work_state, @default_work_state) in [:available, :thinking]
    end)
    |> Enum.map(&elem(&1, 0))
    |> Enum.sort()
  end

  @spec leave(t(), principal_id(), integer()) :: {t(), [effect()]}
  def leave(%__MODULE__{} = state, principal_id, now) do
    if present?(state, principal_id) do
      state =
        state
        |> update_in([Access.key!(:presence)], &Map.delete(&1, principal_id))
        |> Map.update!(:queue, fn q -> Enum.reject(q, &(&1 == principal_id)) end)

      effects = [{:presence, :left, principal_id}]

      # Dropping off while holding the floor must not stall the channel.
      if floor_holder(state) == principal_id do
        {state, release} = release_floor(state, "floor released — #{principal_id} left", now)
        {state, effects ++ release}
      else
        {state, effects}
      end
    else
      {state, []}
    end
  end

  # ── the floor ──────────────────────────────────────────────────────────────

  @doc """
  A principal asks to speak.

  In `:open` mode this is a no-op that succeeds: there is no floor to hold,
  and telling a client "granted" keeps one code path on the client side for
  every mode.
  """
  @spec request_floor(t(), principal_id(), integer()) :: {t(), [effect()]}
  def request_floor(%__MODULE__{mode: :open} = state, principal_id, now) do
    {state, [{:grant, principal_id, now}]}
  end

  def request_floor(%__MODULE__{} = state, principal_id, now) do
    cond do
      not present?(state, principal_id) ->
        {state, [{:violation, principal_id, "not joined to this channel"}]}

      floor_holder(state) == principal_id ->
        {state, [{:grant, principal_id, state.floor.expires_at}]}

      principal_id in state.queue ->
        {state, [{:queued, principal_id, position_of(state.queue, principal_id)}]}

      state.floor == nil and state.mode == :round_robin ->
        grant(state, principal_id, now)

      true ->
        # Someone is speaking, or the channel is moderated and waiting for a
        # handoff. Either way the answer is "wait", with a real position so
        # the client can say how long.
        queue = state.queue ++ [principal_id]
        state = %{state | queue: queue}
        {state, [{:queued, principal_id, position_of(queue, principal_id)}]}
    end
  end

  @doc """
  The floor holder passes it on. In `:moderated` mode a staff principal may
  hand the floor to anyone, which is the only way it ever moves there.
  """
  @spec handoff(t(), principal_id(), principal_id(), integer()) :: {t(), [effect()]}
  def handoff(%__MODULE__{} = state, from, to, now) do
    cond do
      not present?(state, to) ->
        {state, [{:violation, from, "cannot hand the floor to someone who is not here"}]}

      floor_holder(state) == from or (state.mode == :moderated and staff?(state, from)) ->
        state = %{state | queue: Enum.reject(state.queue, &(&1 == to))}
        grant(state, to, now)

      true ->
        {state, [{:violation, from, "only the floor holder can hand it on"}]}
    end
  end

  @doc """
  An event was actually published to the relay, and Vantage's indexer saw it.

  This is where compliance is judged. It also spends rate budget, in every
  mode — an open forum still needs a runaway agent throttled.
  """
  @spec observed(t(), principal_id(), String.t(), integer()) :: {t(), [effect()]}
  def observed(%__MODULE__{} = state, principal_id, msg_type, now) do
    {state, budget_effects} = spend(state, principal_id, now)

    flow_effects =
      cond do
        state.mode == :open -> []
        msg_type == "system" -> []
        floor_holder(state) == principal_id -> []
        true -> [{:violation, principal_id, "posted without the floor"}]
      end

    # A compliant message ends the turn. An out-of-turn one does not — the
    # rightful holder keeps their floor rather than losing it to a
    # queue-jumper, which would make jumping the queue a winning move.
    {state, advance_effects} =
      if flow_effects == [] and state.mode != :open and floor_holder(state) == principal_id do
        release_floor(state, "turn complete", now)
      else
        {state, []}
      end

    {state, budget_effects ++ flow_effects ++ advance_effects}
  end

  @doc "Expire a floor whose time is up, and pass it on."
  @spec tick(t(), integer()) :: {t(), [effect()]}
  def tick(%__MODULE__{floor: nil} = state, _now), do: {state, []}

  def tick(%__MODULE__{floor: %{principal_id: id, expires_at: at}} = state, now)
      when now >= at do
    release_floor(state, "floor timed out for #{id}", now)
  end

  def tick(state, _now), do: {state, []}

  # ── internals ──────────────────────────────────────────────────────────────

  defp grant(%__MODULE__{} = state, principal_id, now) do
    expires_at = now + state.floor_ttl_ms
    state = %{state | floor: %{principal_id: principal_id, granted_at: now, expires_at: expires_at}}

    {state,
     [
       {:grant, principal_id, expires_at},
       # The grant goes into the durable log too. A transcript read back next
       # month has to explain its own turn-taking, not just its content.
       {:system, "floor granted to #{principal_id}"}
     ]}
  end

  defp release_floor(%__MODULE__{} = state, reason, now) do
    state = %{state | floor: nil}
    effects = [{:system, reason}]

    case next_present(state) do
      nil ->
        {state, effects}

      {next_id, rest} ->
        {state, grant_effects} = grant(%{state | queue: rest}, next_id, now)
        {state, effects ++ grant_effects}
    end
  end

  # Skip anyone who queued and then disconnected, rather than granting the
  # floor to an empty seat and waiting out the whole TTL.
  defp next_present(%__MODULE__{queue: queue} = state) do
    case Enum.split_while(queue, &(not present?(state, &1))) do
      {_gone, []} -> nil
      {_gone, [next | rest]} -> {next, rest}
    end
  end

  defp position_of(queue, principal_id) do
    Enum.find_index(queue, &(&1 == principal_id)) + 1
  end

  defp staff?(%__MODULE__{staff: staff}, principal_id), do: MapSet.member?(staff, principal_id)

  defp spend(%__MODULE__{} = state, principal_id, now) do
    bucket = Map.get(state.budgets, principal_id, %{tokens: @bucket_capacity, at: now})
    refilled = min(@bucket_capacity, bucket.tokens + div(now - bucket.at, @bucket_refill_ms))

    if refilled <= 0 do
      retry_after = @bucket_refill_ms - rem(now - bucket.at, @bucket_refill_ms)
      {state, [{:rate_limited, principal_id, retry_after}]}
    else
      budgets = Map.put(state.budgets, principal_id, %{tokens: refilled - 1, at: now})
      {%{state | budgets: budgets}, []}
    end
  end
end
