defmodule FeedSupervisor.HTTP do
  @moduledoc "GET /health -> JSON per worker. Bound to 127.0.0.1 only."
  use Plug.Router

  plug(:match)
  plug(:dispatch)

  get "/health" do
    send_json(conn, 200, FeedSupervisor.Health.snapshot())
  end

  get "/health/:name" do
    case FeedSupervisor.Health.snapshot().workers do
      %{^name => w} -> send_json(conn, 200, w)
      _ -> send_json(conn, 404, %{error: "no such worker", name: name})
    end
  end

  match _ do
    send_json(conn, 404, %{error: "not found"})
  end

  defp send_json(conn, code, body) do
    conn
    |> Plug.Conn.put_resp_content_type("application/json")
    |> Plug.Conn.send_resp(code, Jason.encode!(body))
  end

  def child_spec(opts) do
    port = Keyword.get_lazy(opts, :port, &port/0)

    Plug.Cowboy.child_spec(
      scheme: :http,
      plug: __MODULE__,
      ref: Keyword.get(opts, :ref, __MODULE__),
      options: [ip: {127, 0, 0, 1}, port: port]
    )
  end

  def port, do: String.to_integer(System.get_env("FEED_SUPERVISOR_PORT") || "8094")
end
