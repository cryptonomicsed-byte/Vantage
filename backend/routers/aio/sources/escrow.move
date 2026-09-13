module aio::escrow {
    use sui::object::{Self, UID};
    use sui::tx_context::{Self, TxContext, sender};
    use sui::transfer;
    use sui::coin::{Self, Coin, into_balance, from_balance, value};
    use sui::balance::{Self, Balance, split, join};
    use aio::events;

    /// Generic coin escrow between client and worker.
    public struct Escrow<T> has key, store {
        id: UID,
        client: address,
        worker: address,
        amount: Balance<T>,
        rid: vector<u8>,
        released: bool,
        refunded: bool,
    }

    /// Create escrow with full-amount funding.
    public entry fun create<T>(client: address, worker: address, coins: Coin<T>, rid: vector<u8>, ctx: &mut TxContext): Escrow<T> {
        assert!(client != worker, 2001);
        let bal = into_balance<T>(coins);
        Escrow<T>{ id: object::new(ctx), client, worker, amount: bal, rid, released: false, refunded: false }
    }

    /// Release to worker — only client can release.
    public entry fun release<T>(e: &mut Escrow<T>, ctx: &mut TxContext) {
        assert!(sender(ctx) == e.client, 2004);
        assert!(!e.released && !e.refunded, 2002);
        let coin_out = from_balance<T>(e.amount);
        e.released = true;
        // Emit receipt with actual amount.
        events::emit_work_receipt(e.client, e.worker, e.rid, sui::coin::value(&coin_out));
        transfer::transfer(coin_out, e.worker);
    }

    /// Refund to client — only client can refund.
    public entry fun refund<T>(e: &mut Escrow<T>, ctx: &mut TxContext) {
        assert!(sender(ctx) == e.client, 2005);
        assert!(!e.released && !e.refunded, 2003);
        let coin_out = from_balance<T>(e.amount);
        e.refunded = true;
        transfer::transfer(coin_out, e.client);
    }
}
