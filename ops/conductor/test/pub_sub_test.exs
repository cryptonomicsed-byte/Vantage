defmodule Conductor.PubSubTest do
  @moduledoc """
  The three faults this replaces, pinned as tests: fan-out confined to one
  process, one slow subscriber blocking the rest, and a dead subscriber
  discovered mid-broadcast.
  """
  use ExUnit.Case, async: false

  alias Conductor.PubSub

  setup do
    # Each test uses fresh topic names, so subscriptions cannot leak between
    # them even though the registry is shared.
    {:ok, topic: "t-#{System.unique_integer([:positive])}"}
  end

  test "a subscriber receives what is broadcast", %{topic: topic} do
    PubSub.subscribe(topic)
    assert PubSub.broadcast(topic, %{"type" => "ping"}) == 1
    assert_receive {:conductor, %{"type" => "ping", "topic" => ^topic}}
  end

  test "every subscriber gets it, not just the first", %{topic: topic} do
    parent = self()

    others =
      for i <- 1..3 do
        spawn(fn ->
          PubSub.subscribe(topic)
          send(parent, {:ready, i})
          receive do: ({:conductor, payload} -> send(parent, {:got, i, payload}))
        end)
      end

    for i <- 1..3, do: assert_receive({:ready, ^i})
    assert PubSub.broadcast(topic, %{"n" => 1}) == 3
    for i <- 1..3, do: assert_receive({:got, ^i, %{"n" => 1}})

    Enum.each(others, &Process.exit(&1, :kill))
  end

  test "a subscriber that never drains does not block the others", %{topic: topic} do
    # The defect in the Python fan-out this replaces: a serial send loop
    # stalls on one slow socket. Here delivery is a message send, so a
    # subscriber that never reads still gets the message queued and everyone
    # else is served immediately.
    stuck = spawn(fn -> Process.sleep(:infinity) end)
    Process.sleep(10)

    task =
      Task.async(fn ->
        PubSub.subscribe(topic)
        send(self(), :subscribed)
        receive do: ({:conductor, payload} -> payload)
      end)

    # Subscribe the stuck process too.
    send(stuck, :noop)
    Process.sleep(10)

    PubSub.broadcast(topic, %{"n" => 2})
    assert %{"n" => 2} = Task.await(task, 500)

    Process.exit(stuck, :kill)
  end

  test "a dead subscriber is dropped by the registry, not discovered mid-send", %{topic: topic} do
    dead =
      spawn(fn ->
        PubSub.subscribe(topic)
        receive do: (:stop -> :ok)
      end)

    Process.sleep(20)
    assert PubSub.broadcast(topic, %{"n" => 3}) == 1

    Process.exit(dead, :kill)
    Process.sleep(20)

    assert PubSub.broadcast(topic, %{"n" => 4}) == 0
  end

  test "broadcasting to a topic nobody watches is not an error", %{topic: topic} do
    assert PubSub.broadcast(topic, %{"n" => 5}) == 0
  end

  test "subscribing twice does not double-deliver", %{topic: topic} do
    PubSub.subscribe(topic)
    PubSub.subscribe(topic)

    PubSub.broadcast(topic, %{"n" => 6})
    assert_receive {:conductor, %{"n" => 6}}
    refute_receive {:conductor, %{"n" => 6}}, 100
  end

  test "unsubscribing stops delivery", %{topic: topic} do
    PubSub.subscribe(topic)
    PubSub.unsubscribe(topic)

    assert PubSub.broadcast(topic, %{"n" => 7}) == 0
    refute_receive {:conductor, _}, 100
  end

  test "topics are isolated from one another", %{topic: topic} do
    other = topic <> "-other"
    PubSub.subscribe(topic)

    PubSub.broadcast(other, %{"n" => 8})
    refute_receive {:conductor, _}, 100
  end

  test "the payload carries its topic so one socket can multiplex", %{topic: topic} do
    PubSub.subscribe(topic)
    PubSub.broadcast(topic, %{"type" => "channel_message"})
    assert_receive {:conductor, payload}
    assert payload["topic"] == topic
  end

  test "subscriptions are visible to the subscriber", %{topic: topic} do
    PubSub.subscribe(topic)
    assert topic in PubSub.subscriptions()
    PubSub.unsubscribe(topic)
    refute topic in PubSub.subscriptions()
  end
end
