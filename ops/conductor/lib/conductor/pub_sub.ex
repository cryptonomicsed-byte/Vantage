defmodule Conductor.PubSub do
  @moduledoc """
  Topic fan-out for live UI and agent feeds.

  This is Phase 4's reason for existing. Vantage's own fan-out
  (`_gossip_channels` in backend/main.py) is a plain map of socket sets held
  in one Python process, sent to serially. That has three real faults: it
  cannot span more than one worker, so with two workers half the subscribers
  miss every event; one stalled socket blocks delivery to everyone behind it
  in the loop; and a restart drops every subscription.

  A `Registry` with duplicate keys fixes all three at once. Each subscriber
  is a process, delivery is a message send that cannot block on a slow peer,
  and a dead subscriber is removed by the registry rather than discovered
  mid-broadcast.

  Topics are opaque strings chosen by the backend (`guild.acme`,
  `swarm.system.alerts`, `block.17`), so this module never needs to know what
  they mean — authorization is the backend's to decide before it forwards.
  """
  require Logger

  @registry Conductor.TopicRegistry

  @doc """
  Subscribe the calling process to a topic. Idempotent.

  The idempotence is load-bearing rather than tidy: a duplicate-key registry
  will happily register the same process twice, and `Registry.register`
  returns `{:ok, _}` both times — there is no `:already_registered` error to
  catch here, unlike a `:unique` registry. A client that resubscribes after a
  reconnect would then receive every event twice, so the existing
  registration has to be checked for explicitly.
  """
  @spec subscribe(String.t()) :: :ok | {:error, term()}
  def subscribe(topic) when is_binary(topic) do
    if topic in Registry.keys(@registry, self()) do
      :ok
    else
      case Registry.register(@registry, topic, nil) do
        {:ok, _} -> :ok
        error -> error
      end
    end
  end

  @doc "Unsubscribe the calling process from a topic."
  @spec unsubscribe(String.t()) :: :ok
  def unsubscribe(topic) when is_binary(topic) do
    Registry.unregister(@registry, topic)
    :ok
  end

  @doc """
  Send an event to every subscriber of a topic. Returns how many got it.

  Delivery is a plain message send per subscriber, so a subscriber that is
  slow to drain its mailbox delays only itself.
  """
  @spec broadcast(String.t(), map()) :: non_neg_integer()
  def broadcast(topic, event) when is_binary(topic) and is_map(event) do
    payload = Map.merge(event, %{"topic" => topic})

    Registry.dispatch(@registry, topic, fn subscribers ->
      Enum.each(subscribers, fn {pid, _} -> send(pid, {:conductor, payload}) end)
    end)

    Registry.count_match(@registry, topic, nil)
  end

  @doc "Topics the calling process is subscribed to."
  @spec subscriptions() :: [String.t()]
  def subscriptions do
    Registry.keys(@registry, self())
  end
end
