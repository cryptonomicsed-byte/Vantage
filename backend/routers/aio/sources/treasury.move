module aio::treasury {
    /// Placeholder calculator (Phase-1): no mutable treasury state on-chain.
    public fun ubi_cap_from_inflows(inflows_90d: u64): u64 { inflows_90d * 70 / 100 }
}
