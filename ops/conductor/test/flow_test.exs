defmodule Conductor.FlowTest do
  use ExUnit.Case, async: true

  alias Conductor.Flow

  # `now` is supplied by the caller, so these are all deterministic — no
  # sleeping, no timer races.
  @t0 1_000_000

  defp joined(mode, ids, opts \\ []) do
    state = Flow.new([mode: mode] ++ opts)

    Enum.reduce(ids, state, fn id, acc ->
      {acc, _} = Flow.join(acc, id, %{name: "p#{id}"}, @t0)
      acc
    end)
  end

  describe "open channels" do
    test "grant is immediate and needs no queue" do
      state = joined(:open, [1, 2])
      {_state, effects} = Flow.request_floor(state, 1, @t0)
      assert [{:grant, 1, _}] = effects
    end

    test "posting without asking is not a violation" do
      state = joined(:open, [1, 2])
      {_state, effects} = Flow.observed(state, 2, "say", @t0)
      refute Enum.any?(effects, &match?({:violation, _, _}, &1))
    end
  end

  describe "round robin" do
    test "the first request takes the floor" do
      state = joined(:round_robin, [1, 2])
      {state, effects} = Flow.request_floor(state, 1, @t0)

      assert Flow.floor_holder(state) == 1
      assert Enum.any?(effects, &match?({:grant, 1, _}, &1))
    end

    test "the grant is written into the transcript, not just the socket" do
      state = joined(:round_robin, [1])
      {_state, effects} = Flow.request_floor(state, 1, @t0)

      assert Enum.any?(effects, fn
               {:system, text} -> text =~ "floor granted to 1"
               _ -> false
             end)
    end

    test "a second request queues with a real position" do
      state = joined(:round_robin, [1, 2, 3])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, e2} = Flow.request_floor(state, 2, @t0)
      {state, e3} = Flow.request_floor(state, 3, @t0)

      assert {:queued, 2, 1} in e2
      assert {:queued, 3, 2} in e3
      assert Flow.queue(state) == [2, 3]
    end

    test "asking twice does not take two places in the queue" do
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)

      assert Flow.queue(state) == [2]
    end

    test "a completed turn passes the floor to whoever is next" do
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)

      {state, effects} = Flow.observed(state, 1, "say", @t0 + 10)

      assert Flow.floor_holder(state) == 2
      assert Enum.any?(effects, &match?({:grant, 2, _}, &1))
      assert Flow.queue(state) == []
    end

    test "a principal who is not present cannot claim the floor" do
      state = joined(:round_robin, [1])
      {state, effects} = Flow.request_floor(state, 99, @t0)

      assert Flow.floor_holder(state) == nil
      assert [{:violation, 99, reason}] = effects
      assert reason =~ "not joined"
    end
  end

  describe "out-of-turn posts" do
    test "are recorded as violations, never blocked" do
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)

      {_state, effects} = Flow.observed(state, 2, "say", @t0 + 5)

      assert Enum.any?(effects, fn
               {:violation, 2, reason} -> reason =~ "without the floor"
               _ -> false
             end)
    end

    test "do not steal the floor from its rightful holder" do
      # Otherwise jumping the queue would be the winning move.
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.observed(state, 2, "say", @t0 + 5)

      assert Flow.floor_holder(state) == 1
    end
  end

  describe "handoff" do
    test "the holder can pass the floor on" do
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, effects} = Flow.handoff(state, 1, 2, @t0 + 5)

      assert Flow.floor_holder(state) == 2
      assert Enum.any?(effects, &match?({:grant, 2, _}, &1))
    end

    test "someone who does not hold it cannot" do
      state = joined(:round_robin, [1, 2, 3])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, effects} = Flow.handoff(state, 3, 2, @t0 + 5)

      assert Flow.floor_holder(state) == 1
      assert [{:violation, 3, _}] = effects
    end

    test "the floor cannot be handed to an empty seat" do
      state = joined(:round_robin, [1])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, effects} = Flow.handoff(state, 1, 42, @t0 + 5)

      assert Flow.floor_holder(state) == 1
      assert [{:violation, 1, reason}] = effects
      assert reason =~ "not here"
    end

    test "handing off removes the target from the queue" do
      state = joined(:round_robin, [1, 2, 3])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)
      {state, _} = Flow.request_floor(state, 3, @t0)
      {state, _} = Flow.handoff(state, 1, 3, @t0 + 5)

      assert Flow.floor_holder(state) == 3
      assert Flow.queue(state) == [2]
    end
  end

  describe "moderated channels" do
    test "requests queue rather than granting themselves" do
      state = joined(:moderated, [1, 2], staff: [9])
      {state, effects} = Flow.request_floor(state, 1, @t0)

      assert Flow.floor_holder(state) == nil
      assert [{:queued, 1, 1}] = effects
    end

    test "staff can hand the floor to anyone" do
      state = joined(:moderated, [1, 2, 9], staff: [9])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, effects} = Flow.handoff(state, 9, 1, @t0 + 5)

      assert Flow.floor_holder(state) == 1
      assert Enum.any?(effects, &match?({:grant, 1, _}, &1))
    end

    test "a non-staff bystander cannot" do
      state = joined(:moderated, [1, 2], staff: [9])
      {state, effects} = Flow.handoff(state, 2, 1, @t0)

      assert Flow.floor_holder(state) == nil
      assert [{:violation, 2, _}] = effects
    end
  end

  describe "timeouts" do
    test "an expired floor passes to the next in line" do
      state = joined(:round_robin, [1, 2], floor_ttl_ms: 1_000)
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)

      {state, effects} = Flow.tick(state, @t0 + 1_001)

      assert Flow.floor_holder(state) == 2

      assert Enum.any?(effects, fn
               {:system, text} -> text =~ "timed out"
               _ -> false
             end)
    end

    test "a floor still in time is left alone" do
      state = joined(:round_robin, [1], floor_ttl_ms: 1_000)
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, effects} = Flow.tick(state, @t0 + 500)

      assert Flow.floor_holder(state) == 1
      assert effects == []
    end
  end

  describe "disconnects" do
    test "leaving with the floor releases it immediately" do
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)

      {state, effects} = Flow.leave(state, 1, @t0 + 5)

      assert Flow.floor_holder(state) == 2
      assert {:presence, :left, 1} in effects
    end

    test "a queued principal who vanishes is skipped, not waited on" do
      state = joined(:round_robin, [1, 2, 3])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {state, _} = Flow.request_floor(state, 2, @t0)
      {state, _} = Flow.request_floor(state, 3, @t0)

      {state, _} = Flow.leave(state, 2, @t0 + 1)
      {state, _} = Flow.observed(state, 1, "say", @t0 + 2)

      assert Flow.floor_holder(state) == 3
    end

    test "a reconnect is not announced as a new arrival" do
      state = joined(:round_robin, [1])
      {_state, effects} = Flow.join(state, 1, %{name: "p1"}, @t0 + 10)
      assert effects == []
    end
  end

  describe "rate budget" do
    test "a burst is throttled in every mode, open included" do
      state = joined(:open, [1])

      {state, effects} =
        Enum.reduce(1..40, {state, []}, fn _, {s, acc} ->
          {s, e} = Flow.observed(s, 1, "say", @t0)
          {s, acc ++ e}
        end)

      assert Enum.any?(effects, &match?({:rate_limited, 1, _}, &1))
      assert Flow.floor_holder(state) == nil
    end

    test "budget refills over time" do
      state = joined(:open, [1])

      {state, _} =
        Enum.reduce(1..40, {state, []}, fn _, {s, acc} ->
          {s, e} = Flow.observed(s, 1, "say", @t0)
          {s, acc ++ e}
        end)

      {_state, effects} = Flow.observed(state, 1, "say", @t0 + 60_000)
      refute Enum.any?(effects, &match?({:rate_limited, _, _}, &1))
    end

    test "one principal's burst does not throttle another" do
      state = joined(:open, [1, 2])

      {state, _} =
        Enum.reduce(1..40, {state, []}, fn _, {s, acc} ->
          {s, e} = Flow.observed(s, 1, "say", @t0)
          {s, acc ++ e}
        end)

      {_state, effects} = Flow.observed(state, 2, "say", @t0)
      refute Enum.any?(effects, &match?({:rate_limited, _, _}, &1))
    end
  end

  describe "system events" do
    test "are never judged as out-of-turn" do
      state = joined(:round_robin, [1, 2])
      {state, _} = Flow.request_floor(state, 1, @t0)
      {_state, effects} = Flow.observed(state, 2, "system", @t0 + 5)

      refute Enum.any?(effects, &match?({:violation, _, _}, &1))
    end
  end

  describe "work state" do
    test "a principal starts available" do
      state = joined(:open, [1])
      assert Flow.work_state(state, 1) == :available
    end

    test "declaring a state announces it once" do
      state = joined(:open, [1])
      {state, effects} = Flow.set_work_state(state, 1, :working, @t0)
      assert effects == [{:presence, :state, 1}]
      assert Flow.work_state(state, 1) == :working
    end

    test "re-declaring the same state says nothing" do
      # A runtime that heartbeats its status must not flood the channel.
      state = joined(:open, [1])
      {state, _} = Flow.set_work_state(state, 1, :working, @t0)
      {_state, effects} = Flow.set_work_state(state, 1, :working, @t0 + 100)
      assert effects == []
    end

    test "a principal that is not present cannot declare anything" do
      # Otherwise a state is a claim nobody in the room can contradict.
      state = joined(:open, [1])
      {state, effects} = Flow.set_work_state(state, 99, :working, @t0)
      assert effects == []
      assert Flow.work_state(state, 99) == nil
    end

    test "an unknown state changes nothing" do
      state = joined(:open, [1])
      {state, effects} = Flow.set_work_state(state, 1, :vibing, @t0)
      assert effects == []
      assert Flow.work_state(state, 1) == :available
    end

    test "the vocabulary is closed and parses only its own members" do
      assert Flow.parse_work_state("needs_review") == {:ok, :needs_review}
      assert Flow.parse_work_state("almost done") == :error
      assert Flow.parse_work_state(nil) == :error
      assert Flow.parse_work_state(:blocked) == {:ok, :blocked}
    end

    test "a reconnect keeps the declared state" do
      # A dropped socket is not evidence that an agent stopped working.
      state = joined(:open, [1])
      {state, _} = Flow.set_work_state(state, 1, :working, @t0)
      {state, effects} = Flow.join(state, 1, %{name: "p1"}, @t0 + 500)
      assert effects == []
      assert Flow.work_state(state, 1) == :working
    end

    test "leaving clears the state entirely" do
      state = joined(:open, [1])
      {state, _} = Flow.set_work_state(state, 1, :working, @t0)
      {state, _} = Flow.leave(state, 1, @t0 + 10)
      assert Flow.work_state(state, 1) == nil
    end

    test "blocked and needs_review are not available for work" do
      # The reason the vocabulary exists: handing more work to a principal
      # waiting on someone else is how a queue silently stalls.
      state = joined(:open, [1, 2, 3, 4])
      {state, _} = Flow.set_work_state(state, 2, :working, @t0)
      {state, _} = Flow.set_work_state(state, 3, :blocked, @t0)
      {state, _} = Flow.set_work_state(state, 4, :needs_review, @t0)
      assert Flow.available(state) == [1]
    end

    test "thinking still counts as available" do
      state = joined(:open, [1, 2])
      {state, _} = Flow.set_work_state(state, 2, :thinking, @t0)
      assert Flow.available(state) == [1, 2]
    end
  end
end
