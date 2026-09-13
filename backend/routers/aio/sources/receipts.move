module aio::receipts {
    use sui::object::{Self, UID};
    use sui::tx_context::{Self, TxContext};
    use sui::transfer;
    use aio::config;
    use aio::governance;

    /// Active root for a period; Phase-1 elder-gated submission.
    public struct ActiveRoot has key, store {
        id: UID,
        root: vector<u8>,
        count: u64,
        epoch: u64,
    }

    public entry fun submit_root(cfg: &config::Config, root: vector<u8>, count: u64, epoch: u64, ctx: &mut TxContext) {
        governance::require_elder(cfg, ctx);
        let r = ActiveRoot{ id: object::new(ctx), root, count, epoch };
        transfer::share_object(r);
    }
}
