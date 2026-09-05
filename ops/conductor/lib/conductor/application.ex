defmodule Conductor.Application do
  @moduledoc """
  The supervision tree.

  Shallow on purpose. A `ChannelServer` under a `DynamicSupervisor` can die
  and be restarted without touching anything else, because it holds only
  losable state — that isolation is the whole argument for running this tier
  on the BEAM rather than folding it into the Python backend.
  """
  use Application
  require Logger

  @impl true
  def start(_type, _args) do
    Conductor.Backend.start_cache()

    children = [
      {Registry, keys: :unique, name: Conductor.Registry},
      # Duplicate keys: many sockets subscribe to one topic. This is the
      # fan-out that replaces the backend's single-process socket map.
      {Registry, keys: :duplicate, name: Conductor.TopicRegistry},
      {DynamicSupervisor, strategy: :one_for_one, name: Conductor.ChannelSupervisor},
      Conductor.SocketServer
    ]

    # :one_for_one, not :rest_for_one -- an acceptor crash must not take out
    # channels that are mid-turn, and a channel crash must not drop every
    # connected socket.
    Supervisor.start_link(children, strategy: :one_for_one, name: Conductor.Supervisor)
  end
end
