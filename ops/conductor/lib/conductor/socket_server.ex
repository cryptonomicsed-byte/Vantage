defmodule Conductor.SocketServer do
  @moduledoc """
  The Conductor's single listening port, serving two kinds of caller.

    * **Agents and the UI** connect over WebSocket to `/ws` and speak the
      floor protocol.
    * **The Vantage backend** POSTs to `/observed` when its indexer sees a
      message land on the relay, and to `/invalidate` when a channel's
      membership changes.

  One port because there is one service; the path decides which. Backend
  calls carry a shared secret, WebSocket clients present a Vantage
  credential that the backend resolves for us — the Conductor authenticates
  nobody itself.
  """
  require Logger

  alias Conductor.{Backend, ChannelServer, Flow, JSON, PubSub, WS}

  @acceptors 8

  def child_spec(opts) do
    %{id: __MODULE__, start: {__MODULE__, :start_link, [opts]}, type: :supervisor}
  end

  def start_link(opts \\ []) do
    port = Keyword.get(opts, :port, port_from_env())

    listen_opts = [
      :binary,
      packet: :raw,
      active: false,
      reuseaddr: true,
      backlog: 128
    ]

    case :gen_tcp.listen(port, listen_opts) do
      {:ok, socket} ->
        Logger.info("conductor listening on port #{port}")

        children =
          for i <- 1..@acceptors do
            %{
              id: {:acceptor, i},
              start: {Task, :start_link, [fn -> accept_loop(socket) end]},
              restart: :permanent
            }
          end

        Supervisor.start_link(children, strategy: :one_for_one, name: __MODULE__.Supervisor)

      {:error, reason} ->
        {:error, {:listen_failed, port, reason}}
    end
  end

  defp port_from_env do
    case System.get_env("CONDUCTOR_PORT") do
      nil -> 4500
      value -> String.to_integer(value)
    end
  end

  defp accept_loop(listen_socket) do
    case :gen_tcp.accept(listen_socket) do
      {:ok, socket} ->
        {:ok, pid} = Task.start(fn -> serve(socket) end)
        :gen_tcp.controlling_process(socket, pid)
        accept_loop(listen_socket)

      {:error, :closed} ->
        :ok

      {:error, reason} ->
        Logger.warning("accept failed: #{inspect(reason)}")
        accept_loop(listen_socket)
    end
  end

  # ── request dispatch ───────────────────────────────────────────────────────

  defp serve(socket) do
    case read_head(socket, "") do
      {:ok, head, rest} ->
        case WS.parse_request(head) do
          {:ok, method, path, headers} -> route(socket, method, path, headers, rest)
          {:error, _} -> respond_and_close(socket, 400, ~s({"error":"bad request"}))
        end

      {:error, _} ->
        :gen_tcp.close(socket)
    end
  end

  defp route(socket, "GET", "/ws" <> _, headers, rest) do
    case WS.handshake_response(headers) do
      {:ok, response} ->
        :gen_tcp.send(socket, response)
        ws_loop(socket, rest, %{channel_id: nil, principal_id: nil})

      {:error, :not_websocket} ->
        respond_and_close(socket, 400, ~s({"error":"expected a websocket upgrade"}))
    end
  end

  defp route(socket, "GET", "/health", _headers, _rest) do
    respond_and_close(socket, 200, ~s({"status":"ok","service":"conductor"}))
  end

  defp route(socket, "POST", path, headers, rest)
       when path in ["/observed", "/invalidate", "/broadcast"] do
    if authorized?(headers) do
      body = read_body(socket, headers, rest)
      handle_backend_post(socket, path, body)
    else
      respond_and_close(socket, 401, ~s({"error":"bad or missing conductor secret"}))
    end
  end

  defp route(socket, _method, _path, _headers, _rest) do
    respond_and_close(socket, 404, ~s({"error":"not found"}))
  end

  defp authorized?(headers) do
    expected = System.get_env("CONDUCTOR_SHARED_SECRET") || ""
    presented = Map.get(headers, "x-conductor-secret", "")
    # An unset secret means the port is closed to backend calls, not open to
    # everyone -- failing shut is the only safe reading of "not configured".
    expected != "" and secure_compare(expected, presented)
  end

  defp secure_compare(a, b) do
    byte_size(a) == byte_size(b) and :crypto.hash_equals(a, b)
  rescue
    # crypto_hash_equals is OTP 25+; fall back to a non-short-circuiting
    # comparison rather than an early-exit one.
    _ -> constant_time_equal(a, b)
  end

  defp constant_time_equal(a, b) do
    byte_size(a) == byte_size(b) and
      :binary.bin_to_list(a)
      |> Enum.zip(:binary.bin_to_list(b))
      |> Enum.reduce(0, fn {x, y}, acc -> Bitwise.bor(acc, Bitwise.bxor(x, y)) end) == 0
  end

  defp handle_backend_post(socket, "/observed", body) do
    with {:ok, %{"channel_id" => channel_id, "principal_id" => principal_id} = payload} <-
           JSON.decode(body) do
      msg_type = Map.get(payload, "msg_type", "say")
      # Only tell a channel that is actually running. A message in a channel
      # nobody is coordinating needs no arbitration.
      case Registry.lookup(Conductor.Registry, channel_id) do
        [{_pid, _}] -> ChannelServer.observed(channel_id, principal_id, msg_type)
        [] -> :ok
      end

      respond_and_close(socket, 200, ~s({"ok":true}))
    else
      _ -> respond_and_close(socket, 400, ~s({"error":"channel_id and principal_id required"}))
    end
  end

  defp handle_backend_post(socket, "/broadcast", body) do
    case JSON.decode(body) do
      {:ok, %{"topic" => topic} = payload} ->
        event = Map.get(payload, "event", %{})
        delivered = PubSub.broadcast(topic, event)
        respond_and_close(socket, 200, JSON.encode(%{ok: true, delivered: delivered}))

      _ ->
        respond_and_close(socket, 400, ~s({"error":"topic required"}))
    end
  end

  defp handle_backend_post(socket, "/invalidate", body) do
    case JSON.decode(body) do
      {:ok, %{"channel_id" => channel_id}} ->
        Backend.channel_structure(channel_id, force: true)
        respond_and_close(socket, 200, ~s({"ok":true}))

      _ ->
        respond_and_close(socket, 400, ~s({"error":"channel_id required"}))
    end
  end

  # ── websocket session ──────────────────────────────────────────────────────

  defp ws_loop(socket, buffer, session) do
    :inet.setopts(socket, active: :once)

    receive do
      {:tcp, ^socket, data} ->
        case consume(socket, buffer <> data, session) do
          {:ok, rest, session} -> ws_loop(socket, rest, session)
          :close -> close(socket, session)
        end

      {:tcp_closed, ^socket} ->
        depart(session)

      {:tcp_error, ^socket, _reason} ->
        depart(session)

      # Effects pushed from the ChannelServer.
      {:conductor, payload} ->
        :gen_tcp.send(socket, WS.encode_frame({:text, JSON.encode(payload)}))
        ws_loop(socket, buffer, session)
    end
  end

  defp consume(socket, buffer, session) do
    case WS.decode_frame(buffer) do
      :more ->
        {:ok, buffer, session}

      {:ok, :close, _rest} ->
        :close

      {:ok, {:ping, payload}, rest} ->
        :gen_tcp.send(socket, WS.encode_frame({:pong, payload}))
        consume(socket, rest, session)

      {:ok, {:pong, _}, rest} ->
        consume(socket, rest, session)

      {:ok, {:text, text}, rest} ->
        session = handle_message(socket, text, session)
        consume(socket, rest, session)

      {:ok, {:binary, _}, rest} ->
        send_json(socket, %{type: "error", error: "binary frames are not supported"})
        consume(socket, rest, session)

      {:ok, {:unknown, opcode}, rest} ->
        send_json(socket, %{type: "error", error: "unsupported opcode #{opcode}"})
        consume(socket, rest, session)

      {:error, reason} ->
        Logger.debug("closing socket: #{inspect(reason)}")
        :close
    end
  end

  defp handle_message(socket, text, session) do
    case JSON.decode(text) do
      {:ok, %{"op" => op} = message} -> handle_op(socket, op, message, session)
      {:ok, _} -> send_json(socket, %{type: "error", error: "missing op"}) && session
      {:error, reason} -> send_json(socket, %{type: "error", error: "bad json: #{reason}"}) && session
    end
  end

  defp handle_op(socket, "join", message, session) do
    channel_id = message["channel_id"]
    credential = message["credential"] || ""

    case Backend.authenticate(channel_id, credential) do
      {:ok, %{"principal_id" => principal_id} = identity} ->
        ensure_channel(channel_id)
        meta = %{name: Map.get(identity, "display_name", ""), framework: Map.get(identity, "framework", "")}

        case ChannelServer.join(channel_id, principal_id, meta) do
          {:ok, snapshot} ->
            send_json(socket, Map.put(snapshot, :type, "joined"))
            %{session | channel_id: channel_id, principal_id: principal_id}

          {:error, reason} ->
            send_json(socket, %{type: "error", error: "join failed: #{inspect(reason)}"})
            session
        end

      _ ->
        send_json(socket, %{type: "error", error: "not authorized for this channel"})
        session
    end
  end

  defp handle_op(socket, "subscribe", %{"topic" => topic}, session) when is_binary(topic) do
    PubSub.subscribe(topic)
    send_json(socket, %{type: "subscribed", topic: topic})
    session
  end

  defp handle_op(socket, "unsubscribe", %{"topic" => topic}, session) when is_binary(topic) do
    PubSub.unsubscribe(topic)
    send_json(socket, %{type: "unsubscribed", topic: topic})
    session
  end

  defp handle_op(socket, _op, _message, %{principal_id: nil} = session) do
    send_json(socket, %{type: "error", error: "join first"})
    session
  end

  defp handle_op(socket, "request_floor", _message, session) do
    reply_snapshot(socket, ChannelServer.request_floor(session.channel_id, session.principal_id))
    session
  end

  defp handle_op(socket, "handoff", message, session) do
    reply_snapshot(
      socket,
      ChannelServer.handoff(session.channel_id, session.principal_id, message["to"])
    )

    session
  end

  defp handle_op(socket, "state", message, session) do
    case ChannelServer.set_work_state(session.channel_id, session.principal_id, message["state"]) do
      {:ok, snapshot} ->
        send_json(socket, Map.put(snapshot, :type, "state"))

      {:error, :unknown_state} ->
        send_json(socket, %{
          type: "error",
          error: "unknown state; use one of #{Enum.map_join(Flow.work_states(), ", ", &Atom.to_string/1)}"
        })

      {:error, reason} ->
        send_json(socket, %{type: "error", error: inspect(reason)})
    end

    session
  end

  defp handle_op(socket, "snapshot", _message, session) do
    reply_snapshot(socket, ChannelServer.snapshot(session.channel_id))
    session
  end

  defp handle_op(socket, "leave", _message, session) do
    ChannelServer.leave(session.channel_id, session.principal_id)
    send_json(socket, %{type: "left", channel_id: session.channel_id})
    %{session | channel_id: nil, principal_id: nil}
  end

  defp handle_op(socket, op, _message, session) do
    send_json(socket, %{type: "error", error: "unknown op #{op}"})
    session
  end

  defp reply_snapshot(socket, {:ok, snapshot}) do
    send_json(socket, Map.put(snapshot, :type, "state"))
  end

  defp reply_snapshot(socket, {:error, reason}) do
    send_json(socket, %{type: "error", error: inspect(reason)})
  end

  defp ensure_channel(channel_id) do
    case Registry.lookup(Conductor.Registry, channel_id) do
      [{_pid, _}] ->
        :ok

      [] ->
        DynamicSupervisor.start_child(Conductor.ChannelSupervisor, {ChannelServer, channel_id})
        :ok
    end
  end

  defp depart(%{channel_id: nil}), do: :ok

  defp depart(session) do
    ChannelServer.leave(session.channel_id, session.principal_id)
    :ok
  end

  defp close(socket, session) do
    depart(session)
    :gen_tcp.send(socket, WS.encode_frame(:close))
    :gen_tcp.close(socket)
  end

  defp send_json(socket, payload) do
    :gen_tcp.send(socket, WS.encode_frame({:text, JSON.encode(payload)}))
    true
  end

  defp respond_and_close(socket, status, body) do
    :gen_tcp.send(socket, WS.http_response(status, body))
    :gen_tcp.close(socket)
  end

  # ── raw reads ──────────────────────────────────────────────────────────────

  defp read_head(socket, acc) do
    if String.contains?(acc, "\r\n\r\n") do
      [head, rest] = String.split(acc, "\r\n\r\n", parts: 2)
      {:ok, head, rest}
    else
      case :gen_tcp.recv(socket, 0, 10_000) do
        {:ok, data} -> read_head(socket, acc <> data)
        {:error, reason} -> {:error, reason}
      end
    end
  end

  defp read_body(socket, headers, already) do
    length = headers |> Map.get("content-length", "0") |> String.to_integer()
    read_body_bytes(socket, already, length)
  end

  defp read_body_bytes(_socket, acc, length) when byte_size(acc) >= length do
    binary_part(acc, 0, length)
  end

  defp read_body_bytes(socket, acc, length) do
    case :gen_tcp.recv(socket, 0, 5_000) do
      {:ok, data} -> read_body_bytes(socket, acc <> data, length)
      {:error, _} -> acc
    end
  end
end
