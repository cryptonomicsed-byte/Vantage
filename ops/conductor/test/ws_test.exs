defmodule Conductor.WSTest do
  use ExUnit.Case, async: true

  alias Conductor.WS

  # Build a client frame the way a browser would: masked, with a random key.
  defp client_frame(opcode, payload) do
    mask = :crypto.strong_rand_bytes(4)
    masked = mask_payload(payload, mask)
    len = byte_size(payload)

    header =
      cond do
        len < 126 -> <<1::1, 0::3, opcode::4, 1::1, len::7>>
        len < 65_536 -> <<1::1, 0::3, opcode::4, 1::1, 126::7, len::16>>
        true -> <<1::1, 0::3, opcode::4, 1::1, 127::7, len::64>>
      end

    header <> mask <> masked
  end

  defp mask_payload(payload, mask) do
    payload
    |> :binary.bin_to_list()
    |> Enum.with_index()
    |> Enum.map(fn {byte, i} -> Bitwise.bxor(byte, :binary.at(mask, rem(i, 4))) end)
    |> :binary.list_to_bin()
  end

  describe "handshake" do
    test "computes the accept key from the client key" do
      # The example pair from RFC 6455 §1.3.
      headers = %{"upgrade" => "websocket", "sec-websocket-key" => "dGhlIHNhbXBsZSBub25jZQ=="}
      assert {:ok, response} = WS.handshake_response(headers)
      text = IO.iodata_to_binary(response)

      assert text =~ "101 Switching Protocols"
      assert text =~ "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    end

    test "refuses a request that is not an upgrade" do
      assert {:error, :not_websocket} = WS.handshake_response(%{"upgrade" => "h2c"})
      assert {:error, :not_websocket} = WS.handshake_response(%{})
    end

    test "parses a request line and headers" do
      head = "GET /ws?x=1 HTTP/1.1\r\nHost: example\r\nUpgrade: websocket\r\n"
      assert {:ok, "GET", "/ws?x=1", headers} = WS.parse_request(head)
      assert headers["upgrade"] == "websocket"
      assert headers["host"] == "example"
    end
  end

  describe "decoding client frames" do
    test "a short text frame" do
      buffer = client_frame(0x1, ~s({"op":"request_floor"}))
      assert {:ok, {:text, ~s({"op":"request_floor"})}, ""} = WS.decode_frame(buffer)
    end

    test "a medium frame using the 16-bit length" do
      payload = String.duplicate("x", 300)
      assert {:ok, {:text, ^payload}, ""} = WS.decode_frame(client_frame(0x1, payload))
    end

    test "control frames" do
      assert {:ok, {:ping, "hi"}, ""} = WS.decode_frame(client_frame(0x9, "hi"))
      assert {:ok, {:pong, ""}, ""} = WS.decode_frame(client_frame(0xA, ""))
      assert {:ok, :close, ""} = WS.decode_frame(client_frame(0x8, ""))
    end

    test "two frames in one buffer are read one at a time" do
      buffer = client_frame(0x1, "first") <> client_frame(0x1, "second")
      assert {:ok, {:text, "first"}, rest} = WS.decode_frame(buffer)
      assert {:ok, {:text, "second"}, ""} = WS.decode_frame(rest)
    end

    test "a partial frame asks for more rather than failing" do
      full = client_frame(0x1, "a longer payload than we will supply")
      partial = binary_part(full, 0, 8)
      assert :more = WS.decode_frame(partial)
    end

    test "an empty buffer asks for more" do
      assert :more = WS.decode_frame("")
    end
  end

  describe "protocol errors" do
    test "an unmasked client frame is rejected" do
      # RFC 6455 requires masking from the client; accepting it anyway is a
      # known proxy-poisoning footgun.
      unmasked = <<1::1, 0::3, 1::4, 0::1, 2::7, "hi">>
      assert {:error, :unmasked_client_frame} = WS.decode_frame(unmasked)
    end

    test "an oversized frame is rejected before allocating for it" do
      huge = <<1::1, 0::3, 1::4, 1::1, 127::7, 10_000_000::64, 0::32>>
      assert {:error, :frame_too_large} = WS.decode_frame(huge)
    end

    test "a fragmented frame is rejected rather than half-handled" do
      fragment = <<0::1, 0::3, 1::4, 1::1, 2::7, 0::32, "hi">>
      assert {:error, :fragmented_unsupported} = WS.decode_frame(fragment)
    end
  end

  describe "encoding server frames" do
    test "server frames are not masked" do
      <<_fin::1, _rsv::3, opcode::4, masked::1, _len::7, _::binary>> =
        WS.encode_frame({:text, "hello"}) |> IO.iodata_to_binary()

      assert opcode == 0x1
      assert masked == 0
    end

    test "length encoding switches at the right boundaries" do
      for {size, expected_marker} <- [{10, 10}, {300, 126}, {70_000, 127}] do
        <<_::1, _::3, _::4, _::1, marker::7, _::binary>> =
          WS.encode_frame({:text, String.duplicate("x", size)}) |> IO.iodata_to_binary()

        assert marker == expected_marker
      end
    end

    test "a server frame round-trips through the client's own unmasking" do
      encoded = WS.encode_frame({:text, "round trip"}) |> IO.iodata_to_binary()
      <<_header::binary-size(2), payload::binary>> = encoded
      assert payload == "round trip"
    end
  end

  describe "http responses" do
    test "carry a content length and close the connection" do
      text = WS.http_response(200, ~s({"ok":true})) |> IO.iodata_to_binary()
      assert text =~ "HTTP/1.1 200 OK"
      assert text =~ "Content-Length: 11"
      assert text =~ "Connection: close"
      assert String.ends_with?(text, ~s({"ok":true}))
    end
  end
end
