module aio::mode {
    use sui::object::{Self, UID};
    use sui::tx_context::{Self, TxContext, sender};
    use aio::config;

    /// 0=Normal, 1=Degraded, 2=Halted
    public struct Mode has key, store { id: UID, state: u8 }

    public entry fun init(ctx: &mut TxContext) {
        let m = Mode { id: object::new(ctx), state: 0u8 };
        sui::transfer::share_object(m);
    }

    public fun state(m: &Mode): u8 { m.state }

    /// Elder-gated setters (Phase-1: single elder signature is sufficient).
    public entry fun set_normal(cfg: &config::Config, m: &mut Mode, ctx: &mut TxContext) {
        assert!(config::is_elder(cfg, sender(ctx)), 9001);
        m.state = 0u8;
    }
    public entry fun set_degraded(cfg: &config::Config, m: &mut Mode, ctx: &mut TxContext) {
        assert!(config::is_elder(cfg, sender(ctx)), 9001);
        m.state = 1u8;
    }
    public entry fun set_halted(cfg: &config::Config, m: &mut Mode, ctx: &mut TxContext) {
        assert!(config::is_elder(cfg, sender(ctx)), 9001);
        m.state = 2u8;
    }
}
