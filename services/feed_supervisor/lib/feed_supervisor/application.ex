defmodule FeedSupervisor.Application do
  @moduledoc """
  Registry, then the workers (one_for_one; each worker owns its own restart
  policy for the OS process), then the HTTP health listener. On stop (SIGTERM
  to the BEAM -> init:stop) children terminate in reverse order, so HTTP goes
  first and each worker TERMs (then KILLs) its OS process.
  """
  use Application
  require Logger

  @impl true
  def start(_type, _args) do
    workers =
      if Application.get_env(:feed_supervisor, :autostart, true) do
        path = FeedSupervisor.Config.path()
        specs = FeedSupervisor.Config.load!(path)
        Logger.info("feed supervisor: #{length(specs)} worker(s) from #{path}")
        specs
      else
        []
      end

    http =
      if Application.get_env(:feed_supervisor, :http, true),
        do: [FeedSupervisor.HTTP],
        else: []

    children =
      [
        {Registry, keys: :unique, name: FeedSupervisor.Registry},
        %{
          id: FeedSupervisor.Workers,
          type: :supervisor,
          start: {FeedSupervisor.Workers, :start_link, [workers]}
        }
      ] ++ http

    Supervisor.start_link(children, strategy: :rest_for_one, name: FeedSupervisor.Supervisor)
  end
end

defmodule FeedSupervisor.Workers do
  @moduledoc false
  use Supervisor

  def start_link(specs), do: Supervisor.start_link(__MODULE__, specs, name: __MODULE__)

  @impl true
  def init(specs) do
    Supervisor.init(Enum.map(specs, &FeedSupervisor.Worker.child_spec/1),
      strategy: :one_for_one,
      max_restarts: 10,
      max_seconds: 60
    )
  end

  @doc "Start a worker at runtime (used by tests)."
  def start_worker(spec),
    do: Supervisor.start_child(__MODULE__, FeedSupervisor.Worker.child_spec(spec))

  def stop_worker(name) do
    Supervisor.terminate_child(__MODULE__, {FeedSupervisor.Worker, name})
    Supervisor.delete_child(__MODULE__, {FeedSupervisor.Worker, name})
  end
end
