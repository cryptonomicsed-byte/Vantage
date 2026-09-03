defmodule Conductor.MixProject do
  use Mix.Project

  @moduledoc """
  The Conductor: turn arbitration and presence for guild workspace channels.

  Deliberately dependency-free. Two reasons, in order of importance:

    1. It has nothing to hold. The Conductor owns only ephemeral state --
       who has the floor, who is queued, who is present, what each principal
       has spent of its rate budget. Everything durable lives in the relay
       log and Vantage's index. A service with nothing to persist does not
       need a persistence library, and a service that never signs does not
       need a crypto library.

    2. It deploys as a single OTP release with no package fetch, alongside
       the other ops/ sidecars.

  See docs/VANTAGE_SWARM_COORDINATION_SPEC.md §5.
  """

  def project do
    [
      app: :conductor,
      version: "0.1.0",
      elixir: "~> 1.14",
      start_permanent: Mix.env() == :prod,
      deps: deps(),
      elixirc_options: [warnings_as_errors: true]
    ]
  end

  def application do
    [
      # inets/ssl are OTP applications, not packages -- httpc is how the
      # Conductor calls back into the Vantage backend.
      extra_applications: [:logger, :inets, :ssl, :crypto],
      mod: {Conductor.Application, []}
    ]
  end

  defp deps, do: []
end
