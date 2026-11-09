"""
Ògún Master Forge v2 — Julia Confluence Strategy.
Vectorized signal generation; all indicators pre-computed before signal evaluation.
"""

using Statistics

"""
    generate_confluence_signals(open, high, low, close, volume, params)
    → NamedTuple with all indicator arrays and boolean signal vectors

No loops over bars — everything is vectorized. Called by the backtester
and the live signal server.
"""
function generate_confluence_signals(
    open_prices::Vector{Float64},
    high::Vector{Float64},
    low::Vector{Float64},
    close::Vector{Float64},
    volume::Vector{Float64},
    params::BacktestParams
)
    # ── Trend ──────────────────────────────────────────────────────────────────
    hma1      = calculate_hma(open_prices, params.hma_len)
    hma2      = vcat([NaN], hma1[1:end-1])

    conv_line, base_line, lead1, lead2, cloud_top, cloud_bot =
        calculate_ichimoku(high, low)

    # ── Momentum ───────────────────────────────────────────────────────────────
    macd_line, signal_line = calculate_hma_macd(close)
    rsi_vals   = calculate_rsi(close)
    mfi_vals   = calculate_mfi(high, low, close, volume)
    stoch_k, _ = calculate_stochastic(high, low, close)

    # ── Volume / VWAP ──────────────────────────────────────────────────────────
    _, bb_middle, _, _, _ = calculate_bollinger_bands(close)
    obv_vals   = calculate_obv(close, volume)
    obv_sma    = calculate_rolling_sma(obv_vals, 20)
    vol_ratio  = calculate_volume_ratio(volume)
    rolling_vwap = calculate_rolling_vwap(high, low, close, volume)

    # ── Volatility / Trend Strength ────────────────────────────────────────────
    atr_vals   = calculate_atr(high, low, close)
    adx_vals, plus_di, minus_di = calculate_adx(high, low, close)

    n = length(close)

    # ── Scoring (element-wise) ─────────────────────────────────────────────────
    mom_long = map(1:n) do i
        (valid(macd_line, i) && macd_line[i] > signal_line[i] ? 1 : 0) +
        (valid(rsi_vals, i) && rsi_vals[i] > 50.0 ? 1 : 0) +
        (valid(mfi_vals, i) && mfi_vals[i] > 50.0 ? 1 : 0) +
        (valid(stoch_k, i) && stoch_k[i] > 50.0 ? 1 : 0)
    end

    vol_long = map(1:n) do i
        (valid(bb_middle, i) && close[i] > bb_middle[i] &&
         valid(obv_sma, i) && obv_vals[i] > obv_sma[i] ? 1 : 0) +
        (valid(rolling_vwap, i) && close[i] > rolling_vwap[i] ? 1 : 0)
    end

    # ── Core trend conditions ──────────────────────────────────────────────────
    core_long = map(1:n) do i
        valid(hma1, i) && valid(hma2, i) && valid(lead1, i) &&
        valid(lead2, i) && valid(cloud_top, i) && valid(adx_vals, i) &&
        hma1[i] > hma2[i] &&
        lead1[i] > lead2[i] &&
        close[i] > cloud_top[i] &&
        adx_vals[i] > params.min_adx
    end

    core_short = map(1:n) do i
        valid(hma1, i) && valid(hma2, i) && valid(lead1, i) &&
        valid(lead2, i) && valid(cloud_bot, i) && valid(adx_vals, i) &&
        hma1[i] < hma2[i] &&
        lead1[i] < lead2[i] &&
        close[i] < cloud_bot[i] &&
        adx_vals[i] > params.min_adx
    end

    long_signal  = core_long  .& (mom_long .>= params.mom_req) .& (vol_long .>= params.vol_req)
    short_signal = core_short .& ((4 .- mom_long) .>= params.mom_req) .& ((2 .- vol_long) .>= params.vol_req)

    sl_long  = close .- atr_vals .* params.sl_mult
    tp_long  = close .+ atr_vals .* params.tp_mult
    sl_short = close .+ atr_vals .* params.sl_mult
    tp_short = close .- atr_vals .* params.tp_mult

    (
        hma1=hma1, hma2=hma2,
        conv_line=conv_line, base_line=base_line,
        lead1=lead1, lead2=lead2,
        cloud_top=cloud_top, cloud_bot=cloud_bot,
        macd=macd_line, macd_sig=signal_line,
        rsi=rsi_vals, mfi=mfi_vals, stoch_k=stoch_k,
        bb_middle=bb_middle, obv=obv_vals, obv_sma=obv_sma,
        vol_ratio=vol_ratio, rolling_vwap=rolling_vwap,
        atr=atr_vals, adx=adx_vals, plus_di=plus_di, minus_di=minus_di,
        mom_long=mom_long, vol_long=vol_long,
        long_signal=long_signal, short_signal=short_signal,
        sl_long=sl_long, tp_long=tp_long,
        sl_short=sl_short, tp_short=tp_short,
    )
end

# NaN-safe accessor
valid(v::Vector{Float64}, i::Int) = i >= 1 && i <= length(v) && !isnan(v[i])
