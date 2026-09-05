defmodule Conductor.JSON do
  @moduledoc """
  Just enough JSON to talk to the Vantage backend and to WebSocket clients.

  OTP 25 has no `:json` module and Elixir gained `JSON` only in 1.18, so
  something has to fill the gap. Vendoring a library is not an option here
  (no package fetch in this deployment) and the message shapes involved are
  small and known, so a focused codec is the honest answer.

  Deliberately strict rather than clever: it decodes what the backend and the
  client protocol actually send, and raises on anything else instead of
  guessing. Malformed input from a socket should fail loudly at the edge.
  """

  # ── encoding ───────────────────────────────────────────────────────────────

  @spec encode(term()) :: String.t()
  def encode(term), do: IO.iodata_to_binary(do_encode(term))

  defp do_encode(nil), do: "null"
  defp do_encode(true), do: "true"
  defp do_encode(false), do: "false"
  defp do_encode(n) when is_integer(n), do: Integer.to_string(n)
  defp do_encode(n) when is_float(n), do: Float.to_string(n)
  defp do_encode(a) when is_atom(a), do: encode_string(Atom.to_string(a))
  defp do_encode(s) when is_binary(s), do: encode_string(s)

  defp do_encode(list) when is_list(list) do
    ["[", list |> Enum.map(&do_encode/1) |> Enum.intersperse(","), "]"]
  end

  defp do_encode(map) when is_map(map) do
    pairs =
      map
      |> Enum.map(fn {k, v} -> [encode_key(k), ":", do_encode(v)] end)
      |> Enum.intersperse(",")

    ["{", pairs, "}"]
  end

  defp encode_key(k) when is_atom(k), do: encode_string(Atom.to_string(k))
  defp encode_key(k) when is_binary(k), do: encode_string(k)
  defp encode_key(k) when is_integer(k), do: encode_string(Integer.to_string(k))

  defp encode_string(s) do
    [?", escape(s, []), ?"]
  end

  defp escape(<<>>, acc), do: Enum.reverse(acc)
  defp escape(<<?", rest::binary>>, acc), do: escape(rest, ["\\\"" | acc])
  defp escape(<<?\\, rest::binary>>, acc), do: escape(rest, ["\\\\" | acc])
  defp escape(<<?\n, rest::binary>>, acc), do: escape(rest, ["\\n" | acc])
  defp escape(<<?\r, rest::binary>>, acc), do: escape(rest, ["\\r" | acc])
  defp escape(<<?\t, rest::binary>>, acc), do: escape(rest, ["\\t" | acc])
  defp escape(<<?\b, rest::binary>>, acc), do: escape(rest, ["\\b" | acc])
  defp escape(<<?\f, rest::binary>>, acc), do: escape(rest, ["\\f" | acc])

  defp escape(<<c::utf8, rest::binary>>, acc) when c < 0x20 do
    escape(rest, [:io_lib.format("\\u~4.16.0b", [c]) | acc])
  end

  defp escape(<<c::utf8, rest::binary>>, acc), do: escape(rest, [<<c::utf8>> | acc])
  # Lone bytes that are not valid UTF-8 would otherwise crash the encoder.
  defp escape(<<_, rest::binary>>, acc), do: escape(rest, ["\\ufffd" | acc])

  # ── decoding ───────────────────────────────────────────────────────────────

  @doc "Decode JSON text. Returns `{:ok, term}` or `{:error, reason}`."
  @spec decode(binary()) :: {:ok, term()} | {:error, String.t()}
  def decode(binary) when is_binary(binary) do
    case value(skip_ws(binary)) do
      {term, rest} ->
        case skip_ws(rest) do
          <<>> -> {:ok, term}
          trailing -> {:error, "trailing content: #{inspect(binary_part(trailing, 0, min(20, byte_size(trailing))))}"}
        end
    end
  catch
    :throw, {:json, reason} -> {:error, reason}
  end

  @doc "Decode or raise. For call sites where malformed input is a bug."
  @spec decode!(binary()) :: term()
  def decode!(binary) do
    case decode(binary) do
      {:ok, term} -> term
      {:error, reason} -> raise ArgumentError, "invalid JSON: #{reason}"
    end
  end

  defp skip_ws(<<c, rest::binary>>) when c in [?\s, ?\t, ?\n, ?\r], do: skip_ws(rest)
  defp skip_ws(bin), do: bin

  defp value(<<"null", rest::binary>>), do: {nil, rest}
  defp value(<<"true", rest::binary>>), do: {true, rest}
  defp value(<<"false", rest::binary>>), do: {false, rest}
  defp value(<<?", rest::binary>>), do: string(rest, [])
  defp value(<<?[, rest::binary>>), do: array(skip_ws(rest), [])
  defp value(<<?{, rest::binary>>), do: object(skip_ws(rest), %{})
  defp value(<<c, _::binary>> = bin) when c in ?0..?9 or c == ?-, do: number(bin)
  defp value(<<>>), do: throw({:json, "unexpected end of input"})
  defp value(<<c, _::binary>>), do: throw({:json, "unexpected character #{<<c>>}"})

  defp array(<<?], rest::binary>>, []), do: {[], rest}

  defp array(bin, acc) do
    {item, rest} = value(bin)
    acc = [item | acc]

    case skip_ws(rest) do
      <<?,, more::binary>> -> array(skip_ws(more), acc)
      <<?], more::binary>> -> {Enum.reverse(acc), more}
      _ -> throw({:json, "expected , or ] in array"})
    end
  end

  defp object(<<?}, rest::binary>>, acc) when acc == %{}, do: {%{}, rest}

  defp object(<<?", rest::binary>>, acc) do
    {key, rest} = string(rest, [])

    rest =
      case skip_ws(rest) do
        <<?:, more::binary>> -> skip_ws(more)
        _ -> throw({:json, "expected : after object key"})
      end

    {val, rest} = value(rest)
    acc = Map.put(acc, key, val)

    case skip_ws(rest) do
      <<?,, more::binary>> -> object(skip_ws(more), acc)
      <<?}, more::binary>> -> {acc, more}
      _ -> throw({:json, "expected , or } in object"})
    end
  end

  defp object(_, _), do: throw({:json, "expected object key"})

  defp string(<<?", rest::binary>>, acc), do: {IO.iodata_to_binary(Enum.reverse(acc)), rest}
  defp string(<<?\\, ?", rest::binary>>, acc), do: string(rest, ["\"" | acc])
  defp string(<<?\\, ?\\, rest::binary>>, acc), do: string(rest, ["\\" | acc])
  defp string(<<?\\, ?/, rest::binary>>, acc), do: string(rest, ["/" | acc])
  defp string(<<?\\, ?n, rest::binary>>, acc), do: string(rest, ["\n" | acc])
  defp string(<<?\\, ?r, rest::binary>>, acc), do: string(rest, ["\r" | acc])
  defp string(<<?\\, ?t, rest::binary>>, acc), do: string(rest, ["\t" | acc])
  defp string(<<?\\, ?b, rest::binary>>, acc), do: string(rest, ["\b" | acc])
  defp string(<<?\\, ?f, rest::binary>>, acc), do: string(rest, ["\f" | acc])

  defp string(<<?\\, ?u, hex::binary-size(4), rest::binary>>, acc) do
    code = String.to_integer(hex, 16)
    string(rest, [<<code::utf8>> | acc])
  end

  defp string(<<?\\, c, _::binary>>, _acc), do: throw({:json, "bad escape \\#{<<c>>}"})
  defp string(<<>>, _acc), do: throw({:json, "unterminated string"})
  defp string(<<c::utf8, rest::binary>>, acc), do: string(rest, [<<c::utf8>> | acc])

  defp number(bin) do
    {digits, rest} = take_number(bin, [])
    text = IO.iodata_to_binary(Enum.reverse(digits))

    parsed =
      if String.contains?(text, [".", "e", "E"]) do
        String.to_float(normalize_float(text))
      else
        String.to_integer(text)
      end

    {parsed, rest}
  end

  defp take_number(<<c, rest::binary>>, acc)
       when c in ?0..?9 or c in [?-, ?+, ?., ?e, ?E] do
    take_number(rest, [<<c>> | acc])
  end

  defp take_number(rest, acc), do: {acc, rest}

  # Elixir's String.to_float rejects forms JSON permits, like "1e3" or "1.".
  defp normalize_float(text) do
    text = if String.ends_with?(text, "."), do: text <> "0", else: text

    cond do
      String.contains?(text, ".") -> text
      String.contains?(text, "e") -> String.replace(text, "e", ".0e")
      String.contains?(text, "E") -> String.replace(text, "E", ".0E")
      true -> text <> ".0"
    end
  end
end
