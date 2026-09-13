module aio::config {
    use sui::object::{Self, UID};
    use sui::tx_context::{Self, TxContext};
    use sui::transfer;
    use sui::vec_set;

    /// Shared configuration storing elder addresses.
    public struct Config has key, store {
        id: UID,
        elders: vec_set::VecSet<address>,
    }

    /// One-time genesis init: create & share Config with elder list.
    public entry fun init(elders: vector<address>, ctx: &mut TxContext) {
        let mut set = vec_set::empty<address>();
        let n = vector::length(&elders);
        let mut i = 0;
        while (i < n) {
            let a = *vector::borrow(&elders, i);
            if (!vec_set::contains(&set, &a)) vec_set::insert(&mut set, a);
            i = i + 1;
        };
        let cfg = Config { id: object::new(ctx), elders: set };
        transfer::share_object(cfg);
    }

    /// Read-only: is the address an elder?
    public fun is_elder(cfg: &Config, who: address): bool {
        vec_set::contains(&cfg.elders, &who)
    }
}
