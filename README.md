# Finviz to TradingView

Automated stock screener that runs custom Finviz scans and exports results as TradingView-importable watchlists.

## Screening Criteria

### Longs (5 strategies, merged & deduplicated)

Based on **Oliver Kell**'s momentum/breakout methodology:

| Strategy | Key Filters |
|----------|-------------|
| Relative Volume Surge | Avg Vol > 500K, Price > $20, Rel Vol > 3, Beta > 1, Day Up, Above SMA50 & SMA200 |
| Top Gainers | Avg Vol > 500K, Price > $20, Beta > 1, Above SMA50 & SMA200, Signal: Top Gainers |
| Gap Up | Avg Vol > 500K, Price > $20, Beta > 1, Gap Up 3%+, Above SMA50 & SMA200 |
| YTD Momentum | Avg Vol > 500K, Price > $20, Beta > 1.5, YTD +100%+, Above SMA50 & SMA200 |
| 52W New High | Small Cap+, Avg Vol > 1M, Price > $20, Beta > 1.5, New 52W High, Above SMA50 & SMA200 |

### Shorts (1 strategy)

Based on **Lance Breitstein**'s short-selling criteria:

| Strategy | Key Filters |
|----------|-------------|
| Short Candidates | Small Cap+, Current Vol > 20K, Day Down 5%+, Price 20%+ above SMA20 |

### RS - Relative Strength (conditional)

Based on **Oliver Kell**'s relative strength approach. Only runs when both SPY and QQQ drop more than 1% on the day — identifies stocks showing strength in a weak market.

| Strategy | Key Filters |
|----------|-------------|
| Relative Strength | Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA50 & SMA200 |

## Output

```
output/
├── Longs.txt    # Combined long candidates
├── Shorts.txt   # Short candidates
└── RS.txt       # Relative strength (only generated when market condition met)
```

Files are comma-separated ticker symbols (e.g. `AAPL,MSFT,NVDA`), ready for TradingView import.

## Setup

```bash
# Install dependencies
uv sync

# Run manually
uv run main.py
```

## Import to TradingView

1. Open TradingView
2. Right panel -> Watchlist -> Click the list name
3. Select "Import list..."
4. Choose `output/Longs.txt` (or Shorts/RS)

## Automation (Cron)

The script is configured to run daily after US market close:

```bash
# Tue-Sat 6:00 AM HKT = Mon-Fri after market close
# 6 AM HKT is safe for both EDT (2h after close) and EST (1h after close)
0 6 * * 2-6 /path/to/uv run --directory /path/to/finviz_to_tv main.py >> output/cron.log 2>&1
```

## Configuration

All screener parameters are in `config.toml`. You can modify filters, add new screeners, or adjust settings (delay between requests, output format) without touching the code.

## Dependencies

- Python >= 3.12
- [finviz](https://github.com/mariostoev/finviz) - Finviz web scraper (no API key or premium account required)
