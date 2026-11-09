defmodule TradingOSWeb.MCP.Tools.KanbanTools do
  @moduledoc "MCP tool handlers for Kanban card operations."

  alias TradingOSCore.KanbanSupervisor

  def create_card(%{"ticker" => ticker, "workspace_id" => workspace_id} = args, _context) do
    context = Map.get(args, "context", %{})

    case KanbanSupervisor.create_card(ticker, workspace_id, context) do
      {:ok, card_id, _pid} ->
        {:ok, %{card_id: card_id, ticker: ticker, state: "INGESTED", workspace_id: workspace_id}}

      {:error, reason} ->
        {:error, inspect(reason)}
    end
  end

  def create_card(_args, _context), do: {:error, "ticker and workspace_id are required"}

  def move_card(%{"card_id" => card_id, "to_state" => to_state} = args, context) do
    agent_id = Map.get(context, :agent_id, "mcp_client")
    reason = Map.get(args, "reason", "")

    case KanbanSupervisor.move_card(card_id, to_state, agent_id, %{reason: reason}) do
      {:ok, new_state} -> {:ok, new_state}
      {:error, reason} -> {:error, inspect(reason)}
    end
  end

  def move_card(_args, _context), do: {:error, "card_id and to_state are required"}

  def get_card(%{"card_id" => card_id}, _context) do
    case KanbanSupervisor.get_card(card_id) do
      {:error, :card_not_found} -> {:error, "card not found: #{card_id}"}
      state -> {:ok, state}
    end
  end

  def get_card(_args, _context), do: {:error, "card_id is required"}
end
