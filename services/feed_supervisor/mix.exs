defmodule FeedSupervisor.MixProject do
  use Mix.Project

  def project do
    [
      app: :feed_supervisor,
      version: "0.1.0",
      elixir: "~> 1.14",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [extra_applications: [:logger, :inets], mod: {FeedSupervisor.Application, []}]
  end

  # Same pins as ares-swarm-supervisor, so the host's hex cache already has them.
  defp deps do
    [
      {:plug_cowboy, "2.7.3"},
      {:plug, "1.14.2"},
      {:jason, "~> 1.4"},
      {:cowlib, "2.13.0", override: true},
      {:cowboy, "2.10.0", override: true}
    ]
  end
end
