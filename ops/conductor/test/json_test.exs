defmodule Conductor.JSONTest do
  use ExUnit.Case, async: true

  alias Conductor.JSON

  describe "round trips" do
    test "the message shapes this service actually sends" do
      payloads = [
        %{"type" => "grant", "channel_id" => 12, "principal_id" => 3, "expires_at" => 1_700_000_000_000},
        %{"type" => "presence", "event" => "joined", "principal_id" => 7},
        %{"type" => "state", "flow_mode" => "round_robin", "floor" => nil, "queue" => [2, 3]},
        %{"op" => "join", "channel_id" => 1, "credential" => "vantage_abc123"}
      ]

      for payload <- payloads do
        assert {:ok, ^payload} = payload |> JSON.encode() |> JSON.decode()
      end
    end

    test "atom keys and values encode as strings" do
      assert {:ok, decoded} = JSON.encode(%{type: :grant, ok: true}) |> JSON.decode()
      assert decoded == %{"type" => "grant", "ok" => true}
    end

    test "nested structures" do
      value = %{"a" => [1, %{"b" => [true, false, nil]}], "c" => %{"d" => %{}}}
      assert {:ok, ^value} = value |> JSON.encode() |> JSON.decode()
    end

    test "empty containers" do
      assert {:ok, %{}} = JSON.encode(%{}) |> JSON.decode()
      assert {:ok, []} = JSON.encode([]) |> JSON.decode()
    end
  end

  describe "strings" do
    test "escapes what has to be escaped" do
      value = %{"text" => ~s(quote " backslash \\ newline \n tab \t)}
      assert {:ok, ^value} = value |> JSON.encode() |> JSON.decode()
    end

    test "control characters survive the trip" do
      value = %{"text" => <<1, 2, 3>> |> :binary.bin_to_list() |> Enum.map(&<<&1>>) |> Enum.join()}
      assert {:ok, decoded} = value |> JSON.encode() |> JSON.decode()
      assert decoded["text"] == value["text"]
    end

    test "unicode survives the trip" do
      value = %{"name" => "Ada — 日本語 — 🐝"}
      assert {:ok, ^value} = value |> JSON.encode() |> JSON.decode()
    end

    test "invalid utf-8 does not crash the encoder" do
      # A socket can hand us anything; crashing the whole connection because
      # one byte was not valid UTF-8 would be worse than substituting it.
      assert is_binary(JSON.encode(%{"bad" => <<0xFF, 0xFE>>}))
    end
  end

  describe "numbers" do
    test "integers and negatives" do
      assert {:ok, %{"a" => 0, "b" => -42, "c" => 1_700_000_000_000}} =
               JSON.decode(~s({"a":0,"b":-42,"c":1700000000000}))
    end

    test "floats, including exponent forms JSON allows" do
      assert {:ok, %{"a" => a, "b" => b}} = JSON.decode(~s({"a":1.5,"b":1e3}))
      assert a == 1.5
      assert b == 1000.0
    end
  end

  describe "rejecting bad input" do
    test "malformed documents produce an error rather than a crash" do
      bad = [
        "{",
        ~s({"a":}),
        ~s({"a" 1}),
        ~s(["unterminated),
        ~s({"a":1}trailing),
        "",
        "@"
      ]

      for input <- bad do
        assert {:error, _} = JSON.decode(input), "expected #{inspect(input)} to be rejected"
      end
    end

    test "decode! raises where decode returns an error" do
      assert_raise ArgumentError, fn -> JSON.decode!("{oops}") end
    end
  end
end
