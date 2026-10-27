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

- **Real gilt yields** added: Bank of England 5, 10 and 20-year real zero-coupon yields (IUDSRZC,
  IUDMRZC, IUDLRZC). Note the nominal gilt series are par yields, so nominal minus real here is not a
  clean breakeven. **2-year gilt: still open** - no 2-year series answers in the IADB database.

Known behaviour: Yahoo sometimes leaves a day's close empty (26 Oct 2026 for every London line). The
report lists such weekdays; because each run re-reads full history, the day is added if Yahoo fills it.
ETF rows dated today are skipped before 17:00 London so an intraday price is never frozen into history.

## Stage 3 (27 Oct 2026): the daily view
- `build_view.py` - reads the stored history (fetches nothing; standard library only) and builds the
  view: returns over 1 day, 1 week, 1 month, 3 months, year to date and 1 year for every series, by
  layer, all measured to one as-at date (the latest date held by at least half the ETF lines).
  `--as-of YYYY-MM-DD` rebuilds the view as it would have looked on any past date.
  - **ETFs: total return in sterling**, computed from the stored close plus dividends (reinvested on
    the ex-date), never from Yahoo's adjusted close - which on 27 Oct 2026 missed most dividends on
    ISF, MIDD and IWDP (checked against CUKX, the accumulating FTSE 100 line: within 0.1-0.5 points).
  - **Rates:** level, and change in basis points over the same periods.
  - **Relationships:** pairs listed in `instruments.json` under `relationships` - relative total
    return for market pairs, spread and its change for rate pairs. Edit the list freely.
  - **Bad prices:** a close more than 20% from the median of the 11 days around it, confirmed by later
    closes, is left out of the figures and listed on the page (the stored history is not changed).
    A jump that size in the last two days cannot be confirmed yet: that line is blanked and marked check.
    Example: Fri 24 Oct 2025, the trading day before the clock change, Yahoo stored 12 lines in USD
    (about 1.34x) and left most others empty - the same clock-change fault as 26 Oct 2026.
- `view_template.html` - the page; `/*__DATA__*/` is the injection marker (same pattern as
  market-observatory, same colours). Light and dark mode; works at phone width.
- `view/index.html` (generated; do not edit) and `view/view.json` (every figure with its dates).
- The daily workflow builds the view after the history and commits both. If the view fails, the
  history is still committed and the job fails loudly.

Nothing is published: the view is committed to this repository only. Publishing (GitHub Pages) is a
decision still to be taken. To read it now: open `view/index.html` from a local clone in a browser.
