# HK Long-side Metrics Frame (cloud-published)

Daily `<YYYY-MM-DD>.csv` written by `.github/workflows/update_hk_rs.yml`
(`scripts/compute_hk_rs_cloud.py`), reusing the same k-line batch fetched for
the HK RS tables. Indexed by Futu `code` (e.g. `HK.00700`).

Columns are the k-line-derived outputs of `hk_eod.build_metrics_frame`, minus:
- `market_cap` — needs Futu (not available in CI); filled locally from a Futu
  snapshot.
- `above_sma50` / `above_sma200` — recomputed locally from
  `last_price`/`sma50`/`sma200` (avoids bool↔CSV serialization fragility).

The local 20:00 HKT run fetches this via `hk_metrics.build_hk_metrics_cloud`
to skip its own throttled ~2,400-ticker yfinance download. Files older than
14 days are pruned each run.
