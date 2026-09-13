module aio::governance {
    use sui::tx_context::{Self, TxContext, sender};
    use aio::config;

    /// Phase-1 helper: require elder sender.
    public fun require_elder(cfg: &config::Config, ctx: &TxContext) {
        assert!(config::is_elder(cfg, sender(ctx)), 9101)
    }
}
