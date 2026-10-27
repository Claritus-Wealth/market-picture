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

## Stage 2 (27 Oct 2026): data gaps fixed, daily history started
- `build_daily.py` - fetches every series and appends what is new to Minato's own history under
  `data/`. **Append-only**: a stored date is never rewritten, so the files record what was seen on
  the day; later vendor changes are reported as revisions, not applied. The first run backfills.
  - `data/etf/<id>.csv` - date, close in pounds (pence lines divided by 100), dividend in pounds, source.
  - `data/rates/<id>.csv` - date, yield in percent, source.
  - `data/daily/<date>.json` and `data/latest.json` - the day's snapshot; `data/report.md` - the report.
- `.github/workflows/daily-build.yml` - "Daily build": weekdays 21:30 UTC and on demand. Commits the
  history first, then fails the job if any series failed, so GitHub emails the failure.
- `requirements.txt` - yfinance is **required** (GitHub runners are refused by the direct Yahoo route).

Gaps fixed:
- **US 2y and 10y yields** now come from the US Treasury daily par yield CSV (the source FRED itself
  republishes). FRED stays as fallback; it does not answer GitHub runners.
- **World Utilities (XWUS)**: the sterling line was listed only in Aug 2023. Earlier history is taken
  from XDWU.L, the USD line of the same fund (same ISIN), converted at the daily GBP/USD rate. It is used
  only if it agrees with the sterling line where both exist (median gap within 2%; 0.26% on 27 Oct 2026),
  and every spliced row says so in its `source` column.

Known behaviour: Yahoo sometimes leaves a day's close empty (26 Oct 2026 for every London line). The
report lists such weekdays; because each run re-reads full history, the day is added if Yahoo fills it.
ETF rows dated today are skipped before 17:00 London so an intraday price is never frozen into history.

Nothing is published yet. The dashboard follows once the history is running.
