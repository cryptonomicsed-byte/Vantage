module aio::events {
    use sui::event;

    /// Emitted when escrow is released to worker.
    public struct WorkReceiptEvent has copy, drop {
        client: address,
        worker: address,
        rid: vector<u8>,
        amount: u64,
    }

    public fun emit_work_receipt(client: address, worker: address, rid: vector<u8>, amount: u64) {
        event::emit(WorkReceiptEvent{ client, worker, rid, amount })
    }
}
