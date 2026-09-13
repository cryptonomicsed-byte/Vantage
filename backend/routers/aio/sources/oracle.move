module aio::oracle {
    use sui::object::{Self, UID};
    use sui::tx_context::{Self, TxContext, sender};
    use sui::coin::{Self, Coin, into_balance, value, from_balance};
    use sui::balance::{Self, Balance, join};
    use sui::sui::SUI;

    /// Registered operator with bonded SUI (Phase-1 economic gate only).
    public struct Operator has key, store {
        id: UID,
        owner: address,
        pubkey: vector<u8>,
        bond: Balance<SUI>,
        min_bond: u64,
    }

    /// Register with a real SUI bond >= min_bond. Bonds are held in-obj.
    public entry fun register(pubkey: vector<u8>, min_bond: u64, coins: Coin<SUI>, ctx: &mut TxContext) {
        let val = value(&coins);
        assert!(val >= min_bond, 9201);
        let bal = into_balance(coins);
        let mut op = Operator{ id: object::new(ctx), owner: sender(ctx), pubkey, bond: balance::zero<SUI>(), min_bond };
        join(&mut op.bond, bal);
        sui::transfer::share_object(op);
    }
}
