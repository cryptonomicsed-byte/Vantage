import PredictionShell from './prediction/PredictionShell'

/**
 * Trading → Prediction.
 *
 * This file used to hold the entire surface: one dense scroll with the feed,
 * decisions, gate, context and venues competing for the same column. It is now a
 * thin re-export so `TradingSection.tsx`'s import stays stable while the real
 * layout lives in `./prediction/` as nine sub-tabs, mirroring Trading → Analytics.
 *
 * Nothing about the contract changed: read-only telemetry, keyed endpoints,
 * abstentions shown as decisions, and a vacuous Brier rendered as "—" rather
 * than a healthy-looking 0.
 */
export default PredictionShell
