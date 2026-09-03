defmodule Conductor.Backend do
  @moduledoc """
  The Conductor's only outward call: HTTP to the Vantage backend.

  Three things live on the other side of this boundary, all for the same
  reason — the Conductor holds no keys and no database credentials:

    * **Structure.** Flow mode, membership and staff roles are Vantage's to
      know. Fetched at channel start and cached for a short TTL, so a
      membership change takes effect without a restart but a busy channel
      does not hammer the backend.

    * **System events.** `vt=system` messages have to be signed with the
      deployment's instance key. OTP has no BIP-340 schnorr, and more to the
      point the Conductor is specified to hold no signing key at all, so it
      asks the backend to publish on its behalf.

    * **Violations.** Recorded against the principal for scoring.

  Every call fails soft. The Conductor coordinating a channel with slightly
  stale structure is far better than one that stops coordinating because the
  backend blinked.
  """
  require Logger

  @cache :conductor_structure_cache
  @cache_ttl_ms 30_000
  @timeout_ms 5_000

  @spec start_cache() :: :ok
  def start_cache do
    :ets.new(@cache, [:named_table, :public, :set, read_concurrency: true])
    :ok
  rescue
    ArgumentError -> :ok
  end

  defp base_url do
    System.get_env("VANTAGE_URL") || "http://localhost:8001"
  end

  defp shared_secret do
    System.get_env("CONDUCTOR_SHARED_SECRET") || ""
  end

  @doc """
  Channel structure, cached.

  `force: true` bypasses the cache — used when a membership change is
  signalled, so a ban takes effect at once rather than at the next TTL.
  """
  @spec channel_structure(integer(), keyword()) :: {:ok, map()} | {:error, term()}
  def channel_structure(channel_id, opts \\ []) do
    now = System.monotonic_time(:millisecond)

    cached =
      if Keyword.get(opts, :force, false) do
        nil
      else
        case :ets.lookup(@cache, channel_id) do
          [{^channel_id, structure, at}] when now - at < @cache_ttl_ms -> structure
          _ -> nil
        end
      end

    case cached do
      nil ->
        case get("/api/conductor/channels/#{channel_id}") do
          {:ok, structure} ->
            :ets.insert(@cache, {channel_id, structure, now})
            {:ok, structure}

          error ->
            # Serve a stale entry rather than dropping the channel: the
            # Conductor's job is to keep the floor moving.
            case :ets.lookup(@cache, channel_id) do
              [{^channel_id, stale, _}] -> {:ok, stale}
              _ -> error
            end
        end

      structure ->
        {:ok, structure}
    end
  end

  @doc "Ask the backend to sign and publish a `vt=system` event."
  @spec publish_system(integer(), String.t()) :: :ok
  def publish_system(channel_id, text) do
    post("/api/conductor/channels/#{channel_id}/system", %{text: text})
    :ok
  end

  @doc "Record a flow violation against a principal."
  @spec report_violation(integer(), integer(), String.t()) :: :ok
  def report_violation(channel_id, principal_id, reason) do
    post("/api/conductor/violations", %{
      channel_id: channel_id,
      principal_id: principal_id,
      reason: reason
    })

    :ok
  end

  @doc """
  Resolve a client's credential to a principal for a channel.

  The Conductor never inspects an agent key itself — it hands the credential
  to the backend, which owns authentication, and gets back an identity or a
  refusal.
  """
  @spec authenticate(integer(), String.t()) :: {:ok, map()} | {:error, term()}
  def authenticate(channel_id, credential) do
    post("/api/conductor/authenticate", %{channel_id: channel_id, credential: credential})
  end

  # ── transport ──────────────────────────────────────────────────────────────

  defp get(path) do
    request(:get, path, nil)
  end

  defp post(path, body) do
    request(:post, path, Conductor.JSON.encode(body))
  end

  defp request(method, path, body) do
    url = String.to_charlist(base_url() <> path)
    headers = [{~c"x-conductor-secret", String.to_charlist(shared_secret())}]

    req =
      case method do
        :get -> {url, headers}
        :post -> {url, headers, ~c"application/json", body}
      end

    case :httpc.request(method, req,
           [timeout: @timeout_ms, connect_timeout: @timeout_ms],
           body_format: :binary
         ) do
      {:ok, {{_, status, _}, _headers, response}} when status in 200..299 ->
        decode_body(response)

      {:ok, {{_, status, _}, _headers, response}} ->
        Logger.warning("backend #{method} #{path} -> HTTP #{status}: #{inspect(response)}")
        {:error, {:http, status}}

      {:error, reason} ->
        Logger.warning("backend #{method} #{path} unreachable: #{inspect(reason)}")
        {:error, reason}
    end
  end

  defp decode_body(""), do: {:ok, %{}}

  defp decode_body(body) do
    case Conductor.JSON.decode(body) do
      {:ok, term} -> {:ok, term}
      {:error, reason} -> {:error, {:bad_json, reason}}
    end
  end
end
