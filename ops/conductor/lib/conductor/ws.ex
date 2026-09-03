defmodule Conductor.WS do
  @moduledoc """
  Enough of RFC 6455 to serve WebSocket clients, and enough of HTTP/1.1 to
  accept the backend's `POST /observed`.

  Hand-rolled for the same reason as the JSON codec: this deployment cannot
  fetch packages, and the Conductor's wire needs are small and fixed — text
  frames, ping/pong, close. Anything exotic is rejected rather than guessed
  at.
  """

  # RFC 6455 §1.3. Verified against the RFC's own example pair
  # (dGhlIHNhbXBsZSBub25jZQ== -> s3pPLMBiTxaQ9kYGzzhZRbK+xOo=) in ws_test.exs;
  # a transposed digit here fails every handshake, silently, so the test
  # pins it rather than trusting the constant.
  @ws_guid "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

  # A frame larger than this is a client bug or an attack; the Conductor's
  # own protocol messages are a few hundred bytes.
  @max_frame_bytes 64 * 1024

  # ── handshake ──────────────────────────────────────────────────────────────

  @doc "Parse an HTTP request head into `{method, path, headers}`."
  @spec parse_request(binary()) :: {:ok, String.t(), String.t(), map()} | {:error, term()}
  def parse_request(head) do
    case String.split(head, "\r\n") do
      [request_line | header_lines] ->
        case String.split(request_line, " ") do
          [method, path | _] ->
            {:ok, method, path, parse_headers(header_lines)}

          _ ->
            {:error, :bad_request_line}
        end

      _ ->
        {:error, :bad_request}
    end
  end

  defp parse_headers(lines) do
    Enum.reduce(lines, %{}, fn line, acc ->
      case String.split(line, ":", parts: 2) do
        [k, v] -> Map.put(acc, k |> String.trim() |> String.downcase(), String.trim(v))
        _ -> acc
      end
    end)
  end

  @doc "The 101 response for a valid upgrade request."
  @spec handshake_response(map()) :: {:ok, iodata()} | {:error, :not_websocket}
  def handshake_response(headers) do
    with "websocket" <- headers |> Map.get("upgrade", "") |> String.downcase(),
         key when is_binary(key) <- Map.get(headers, "sec-websocket-key") do
      accept = :crypto.hash(:sha, key <> @ws_guid) |> Base.encode64()

      {:ok,
       [
         "HTTP/1.1 101 Switching Protocols\r\n",
         "Upgrade: websocket\r\n",
         "Connection: Upgrade\r\n",
         "Sec-WebSocket-Accept: ",
         accept,
         "\r\n\r\n"
       ]}
    else
      _ -> {:error, :not_websocket}
    end
  end

  @doc "A plain HTTP response, for the ingest endpoint and for errors."
  @spec http_response(pos_integer(), binary()) :: iodata()
  def http_response(status, body) do
    reason =
      case status do
        200 -> "OK"
        400 -> "Bad Request"
        401 -> "Unauthorized"
        404 -> "Not Found"
        _ -> "Error"
      end

    [
      "HTTP/1.1 #{status} #{reason}\r\n",
      "Content-Type: application/json\r\n",
      "Content-Length: #{byte_size(body)}\r\n",
      "Connection: close\r\n\r\n",
      body
    ]
  end

  # ── frames ─────────────────────────────────────────────────────────────────

  @doc """
  Pull one frame off the buffer.

  Returns `{:ok, frame, rest}`, `:more` when the buffer holds a partial
  frame, or `{:error, reason}`.
  """
  @spec decode_frame(binary()) ::
          {:ok, {:text, binary()} | {:binary, binary()} | :close | {:ping, binary()} | {:pong, binary()}, binary()}
          | :more
          | {:error, term()}
  def decode_frame(<<fin::1, _rsv::3, opcode::4, masked::1, len::7, rest::binary>> = buffer) do
    with {:ok, payload_len, rest} <- payload_length(len, rest),
         :ok <- check_size(payload_len),
         {:ok, mask, rest} <- mask_key(masked, rest),
         {:ok, payload, rest} <- take(rest, payload_len) do
      # Fragmented frames are not part of this protocol; every message the
      # Conductor speaks fits in one frame.
      if fin == 0 do
        {:error, :fragmented_unsupported}
      else
        {:ok, to_frame(opcode, unmask(payload, mask)), rest}
      end
    else
      :more -> :more
      {:error, _} = error -> error
      _ -> {:error, {:bad_frame, byte_size(buffer)}}
    end
  end

  def decode_frame(_partial), do: :more

  defp payload_length(126, <<len::16, rest::binary>>), do: {:ok, len, rest}
  defp payload_length(126, _), do: :more
  defp payload_length(127, <<len::64, rest::binary>>), do: {:ok, len, rest}
  defp payload_length(127, _), do: :more
  defp payload_length(len, rest), do: {:ok, len, rest}

  defp check_size(len) when len > @max_frame_bytes, do: {:error, :frame_too_large}
  defp check_size(_), do: :ok

  defp mask_key(1, <<mask::binary-size(4), rest::binary>>), do: {:ok, mask, rest}
  defp mask_key(1, _), do: :more
  # RFC 6455 requires client frames to be masked; an unmasked one is a
  # protocol error rather than something to be lenient about.
  defp mask_key(0, _rest), do: {:error, :unmasked_client_frame}

  defp take(binary, len) when byte_size(binary) >= len do
    <<payload::binary-size(len), rest::binary>> = binary
    {:ok, payload, rest}
  end

  defp take(_binary, _len), do: :more

  defp unmask(payload, mask), do: apply_mask(payload, mask, 0, [])

  defp apply_mask(<<>>, _mask, _i, acc), do: IO.iodata_to_binary(Enum.reverse(acc))

  defp apply_mask(<<byte, rest::binary>>, mask, i, acc) do
    key = :binary.at(mask, rem(i, 4))
    apply_mask(rest, mask, i + 1, [<<Bitwise.bxor(byte, key)>> | acc])
  end

  defp to_frame(0x1, payload), do: {:text, payload}
  defp to_frame(0x2, payload), do: {:binary, payload}
  defp to_frame(0x8, _payload), do: :close
  defp to_frame(0x9, payload), do: {:ping, payload}
  defp to_frame(0xA, payload), do: {:pong, payload}
  defp to_frame(opcode, _payload), do: {:unknown, opcode}

  @doc "Encode a server frame. Server frames are never masked."
  @spec encode_frame({:text, binary()} | {:pong, binary()} | :close) :: iodata()
  def encode_frame({:text, payload}), do: frame(0x1, payload)
  def encode_frame({:pong, payload}), do: frame(0xA, payload)
  def encode_frame(:close), do: frame(0x8, "")

  defp frame(opcode, payload) do
    len = byte_size(payload)

    header =
      cond do
        len < 126 -> <<1::1, 0::3, opcode::4, 0::1, len::7>>
        len < 65_536 -> <<1::1, 0::3, opcode::4, 0::1, 126::7, len::16>>
        true -> <<1::1, 0::3, opcode::4, 0::1, 127::7, len::64>>
      end

    [header, payload]
  end
end
