defmodule FeedSupervisor.Health do
  @moduledoc "Snapshot of every registered worker, in configured start order."

  def snapshot do
    workers =
      FeedSupervisor.Registry
      |> Registry.select([{{:"$1", :"$2", :"$3"}, [], [{{:"$1", :"$2", :"$3"}}]}])
      |> Enum.sort_by(fn {_name, _pid, order} -> order end)
      |> Enum.flat_map(fn {name, pid, _} ->
        try do
          [{name, FeedSupervisor.Worker.status(pid)}]
        catch
          :exit, _ -> []
        end
      end)

    statuses = Enum.map(workers, fn {_, w} -> w.status end)

    %{
      status: if(Enum.any?(statuses, &(&1 == :failed)), do: "degraded", else: "ok"),
      workers: Map.new(workers, fn {name, w} -> {name, Map.delete(w, :name)} end),
      order: Enum.map(workers, fn {name, _} -> name end)
    }
  end
end
