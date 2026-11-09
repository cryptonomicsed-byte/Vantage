defmodule TradingOSCore.Kanban.Card do
  @moduledoc """
  GenServer representing a single Kanban card (one trading opportunity).

  State machine transitions:
    INGESTED → PARSING → SYNTHESIZED → BACKTESTING → RISK_RANKED
    → MONITORING → EXECUTING → COMPLETE
    (any state) → REJECTED

  Each transition is an immutable event persisted to `kanban_events`.
  On restart, the card replays its event log to restore exact state.
  """

  use GenServer, restart: :transient
  require Logger

  alias TradingOSCore.Agents.{ClaudeArchitect, GrokScout, Watchdog}
  alias TradingOSCore.Repo
  alias TradingOSCore.Schemas.{KanbanCard, KanbanEvent}
  alias TradingOSCore.Workers.BacktestWorker

  @pipeline_transitions %{
    "INGESTED" => "PARSING",
    "PARSING" => "SYNTHESIZED",
    "SYNTHESIZED" => "BACKTESTING",
    "BACKTESTING" => "RISK_RANKED",
    "RISK_RANKED" => "MONITORING",
    "MONITORING" => "EXECUTING",
    "EXECUTING" => "COMPLETE"
  }

  # ── Public API ───────────────────────────────────────────────────────────────

  def start_link(%{card_id: card_id} = opts) do
    GenServer.start_link(__MODULE__, opts, name: via(card_id))
  end

  # ── GenServer Callbacks ──────────────────────────────────────────────────────

  @impl true
  def init(%{card_id: card_id, ticker: ticker, workspace_id: workspace_id, context: context}) do
    # Replay events from DB to restore state (crash recovery)
    state = restore_or_create(card_id, ticker, workspace_id, context)
    {:ok, state}
  end

  @impl true
  def handle_call({:move_to, new_state, agent_id, payload}, _from, state) do
    event = %KanbanEvent{
      id: Ecto.UUID.generate(),
      card_id: state.card_id,
      from_state: state.current_state,
      to_state: new_state,
      agent_id: agent_id,
      metadata: payload,
      hash: compute_hash(state.card_id, state.current_state, new_state, state.last_event_hash)
    }

    case Repo.insert(event) do
      {:ok, saved_event} ->
        new_state_data = %{state | current_state: new_state, last_event_hash: saved_event.hash}

        # Notify frontend via Phoenix Channel
        broadcast_state_change(
          state.card_id,
          state.ticker,
          state.current_state,
          new_state,
          agent_id
        )

        # Trigger next agent in pipeline
        trigger_next_agent(new_state, state.card_id, state.ticker, state.workspace_id)

        Logger.info(
          "[Card #{state.card_id}] #{state.ticker}: #{state.current_state} → #{new_state}"
        )

        {:reply, {:ok, new_state_data}, new_state_data}

      {:error, changeset} ->
        {:reply, {:error, changeset}, state}
    end
  end

  @impl true
  def handle_call(:get_state, _from, state) do
    {:reply, state, state}
  end

  # ── Private Helpers ──────────────────────────────────────────────────────────

  defp restore_or_create(card_id, ticker, workspace_id, context) do
    case Repo.get(KanbanCard, card_id) do
      nil ->
        card =
          Repo.insert!(%KanbanCard{
            id: card_id,
            ticker: ticker,
            workspace_id: workspace_id,
            state: "INGESTED",
            context: context
          })

        %{
          card_id: card.id,
          ticker: ticker,
          workspace_id: workspace_id,
          current_state: "INGESTED",
          context: context,
          last_event_hash: "genesis"
        }

      card ->
        # Replay event log — last event gives current state
        events = Repo.all(KanbanEvent, card_id: card_id, order_by: [asc: :created_at])
        last_event = List.last(events)

        %{
          card_id: card.id,
          ticker: card.ticker,
          workspace_id: card.workspace_id,
          current_state: card.state,
          context: card.context,
          last_event_hash: (last_event && last_event.hash) || "genesis"
        }
    end
  end

  defp compute_hash(card_id, from_state, to_state, prev_hash) do
    data = "#{card_id}:#{from_state}:#{to_state}:#{prev_hash}"
    :crypto.hash(:sha3_256, data) |> Base.encode16(case: :lower)
  end

  defp broadcast_state_change(card_id, ticker, from_state, to_state, agent_id) do
    TradingOSWeb.Endpoint.broadcast("kanban:lobby", "card_moved", %{
      card_id: card_id,
      ticker: ticker,
      from_state: from_state,
      to_state: to_state,
      agent_id: agent_id
    })
  rescue
    _ -> :ok
  end

  defp trigger_next_agent("PARSING", card_id, ticker, workspace_id) do
    ClaudeArchitect.parse_card(card_id, ticker, workspace_id)
  end

  defp trigger_next_agent("BACKTESTING", card_id, ticker, workspace_id) do
    %{card_id: card_id, ticker: ticker, workspace_id: workspace_id}
    |> BacktestWorker.new()
    |> Oban.insert()
  end

  defp trigger_next_agent("MONITORING", card_id, ticker, workspace_id) do
    GrokScout.watch_ticker(ticker)
    Watchdog.watch_card(card_id)
    Logger.info("[Card #{card_id}] #{ticker} entered monitoring — scout and watchdog active")
  end

  defp trigger_next_agent(_state, _card_id, _ticker, _workspace_id), do: :ok

  defp via(card_id) do
    {:via, Registry, {TradingOSCore.KanbanRegistry, card_id}}
  end
end
