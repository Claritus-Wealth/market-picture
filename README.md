# Market Picture (internal)

Minato Wealth internal market-analysis project. Public market data only.
System record: Notion -> Project "Market Picture and Regime Analysis System" (scope redefined 26 Oct 2026).

## Purpose
A regular, daily picture of movement across global, regional, UK, style, sector, bond, rate and
real-asset markets, so patterns can be recognised and Minato builds its own history over time.
London-listed sterling ETFs are used as free daily proxies for the indices. Monthly IA sector
averages and MSCI factsheet returns remain the reference points for anything client-facing.

## Stage 1 (this commit): data source test
- `instruments.json` - the agreed list (about 37 ETFs plus 6 rate series).
- `fetch_test.py` - fetches each series once and reports status, currency, history and freshness
  (standard library; uses the yfinance package as a fallback if installed).
- `.github/workflows/fetch-test.yml` - manual workflow ("Data source test") that runs the script
  and commits `test-results/latest.md` and `latest.json`.

Nothing is published yet. The dashboard and the daily build follow once the data is proven.
