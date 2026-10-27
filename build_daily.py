#!/usr/bin/env python3
"""Minato Wealth Market Picture - daily build (stage 2, 27 Oct 2026).

Fetches every series in instruments.json and adds what is new to Minato's own
history. The history is append-only: a date already stored is never rewritten,
so the files record what was seen on the day, not whatever a vendor later says.
The first run backfills from each source's full history.

Writes:
  data/etf/<id>.csv       date, close_gbp, dividend_gbp, source  (pence normalised to pounds)
  data/rates/<id>.csv     date, value, source                    (yields in percent)
  data/daily/<date>.json  that day's snapshot of every series (London date of the run)
  data/latest.json        the same snapshot, always the most recent
  data/report.md          human-readable report (also sent to the GitHub job summary)

ETFs: yfinance (required: on GitHub runners the direct Yahoo chart API is refused),
with the standard-library Yahoo v8 route as fallback. A line may carry a "backfill":
an earlier listing of the same fund in another currency, converted to sterling at the
daily FX rate and used only for dates before the sterling line begins.
Rates: Bank of England IADB CSV; US Treasury daily par yield CSV, with FRED as fallback
(FRED does not answer GitHub runners).
Exit code is 0 unless the script itself breaks; counts go to GITHUB_OUTPUT so the
workflow can fail loudly after the history has been committed.
"""
import csv, datetime, io, json, os, statistics, time, urllib.parse, urllib.request, warnings
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept': 'application/json,text/csv,*/*'}
NOW_LDN = datetime.datetime.now(ZoneInfo('Europe/London'))
TODAY = NOW_LDN.date()
# Before the London close has settled, today's ETF row is an intraday price: leave it out,
# or the append-only history would freeze a price that never closed.
CUTOFF = TODAY.isoformat() if NOW_LDN.hour >= 17 else (TODAY - datetime.timedelta(days=1)).isoformat()
HISTORY_FROM = 2000  # first year asked of the rate sources on a backfill
REVISION_TOL = 0.005  # stored vs freshly fetched, beyond this is reported as a revision

try:  # the python.org Mac framework Python ships without a CA bundle
    import certifi, ssl
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = None

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()

def day(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()

# ---------- ETFs: each returns {'currency', 'source', 'rows': {date: (close, dividend)}} ----------
def yahoo_yf(symbol):
    import yfinance as yf
    t = yf.Ticker(symbol)
    with warnings.catch_warnings():  # pandas deprecation noise from inside yfinance
        warnings.simplefilter('ignore')
        h = t.history(period='max', interval='1d', auto_adjust=False, actions=True)
    if h is None or h.empty:
        raise RuntimeError('yfinance returned no rows')
    cur = None
    try: cur = t.fast_info.get('currency')
    except Exception: pass
    divs = h['Dividends'] if 'Dividends' in h else None
    rows = {}
    for i, c in zip(h.index, h['Close']):
        if c != c: continue
        d = float(divs[i]) if divs is not None and divs[i] == divs[i] else 0.0
        rows[i.date().isoformat()] = (float(c), d)
    return {'source': 'yfinance', 'currency': cur, 'rows': rows}

def yahoo_std(symbol):
    last_err = None
    for attempt in range(2):
        host = 'query1' if attempt == 0 else 'query2'
        url = (f'https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}'
               '?range=max&interval=1d&events=div')
        try:
            res = json.loads(get(url))['chart']['result'][0]
            divs = {day(int(v['date'])): float(v['amount'])
                    for v in ((res.get('events') or {}).get('dividends') or {}).values()}
            closes = res['indicators']['quote'][0].get('close') or []
            rows = {day(t): (float(c), divs.get(day(t), 0.0))
                    for t, c in zip(res.get('timestamp') or [], closes) if c is not None}
            return {'source': 'yahoo-v8', 'currency': res.get('meta', {}).get('currency'), 'rows': rows}
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            time.sleep(3)
    raise RuntimeError(last_err)

def fetch_yahoo(symbol):
    errors = []
    for fn in (yahoo_yf, yahoo_std):
        try:
            return fn(symbol), errors
        except Exception as e:
            errors.append(f'{fn.__name__}: {e}')
    return None, errors

def to_gbp(data):
    """Pence lines (GBp/GBX) to pounds. Anything not sterling is refused."""
    f = {'GBp': 0.01, 'GBX': 0.01, 'GBP': 1.0}.get(data['currency'])
    if f is None:
        raise RuntimeError(f"currency {data['currency']} not sterling")
    return {d: (c * f, v * f) for d, (c, v) in data['rows'].items()}

def backfill(bf, primary):
    """Earlier listing of the same fund, converted to GBP, for dates before the primary starts.
    Used only if it agrees with the primary where both exist (median gap within 2%)."""
    src, e1 = fetch_yahoo(bf['yahoo'])
    fx, e2 = fetch_yahoo(bf['fx'])
    if not src or not fx:
        return {}, f'backfill not fetched: {"; ".join(e1 + e2)[:150]}'
    if src['currency'] != bf['currency']:
        return {}, f"backfill currency {src['currency']}, expected {bf['currency']}"
    fxr = {d: c for d, (c, _) in fx['rows'].items()}  # GBPUSD=X: units of foreign currency per pound
    conv = {d: (c / fxr[d], v / fxr[d]) for d, (c, v) in src['rows'].items() if fxr.get(d)}
    overlap = [conv[d][0] / primary[d][0] - 1 for d in conv if d in primary and primary[d][0]]
    if len(overlap) < 20:
        return {}, f'backfill overlap only {len(overlap)} days, not used'
    gap = statistics.median(abs(x) for x in overlap)
    if gap > 0.02:
        return {}, f'backfill disagrees with the sterling line (median gap {gap:.1%}), not used'
    first = min(primary)
    pre = {d: r for d, r in conv.items() if d < first}
    return pre, f"history before {first} from {bf['yahoo']} at {bf['fx']} ({len(pre)} days, overlap gap {gap:.2%})"

# ---------- Rates: each returns {date: value} ----------
def fetch_boe(code, backfilling):
    start = f'01/Jan/{HISTORY_FROM}' if backfilling else (TODAY - datetime.timedelta(days=60)).strftime('%d/%b/%Y')
    url = ('https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes'
           f'&Datefrom={start}&Dateto=now&SeriesCodes={code}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N')
    txt = get(url).decode('utf-8', 'replace')
    pts = {}
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        if len(r) >= 2 and r[1].strip():
            try:
                pts[datetime.datetime.strptime(r[0].strip(), '%d %b %Y').date().isoformat()] = float(r[1])
            except ValueError:
                pass
    if not pts:
        raise RuntimeError('no rows parsed; first line: ' + txt[:120].replace('\n', ' '))
    return pts

TSY_URL = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/'
           'daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv')
_tsy_cache = {}

def treasury_year(y):
    if y not in _tsy_cache:
        _tsy_cache[y] = list(csv.DictReader(io.StringIO(get(TSY_URL.format(y=y), timeout=90).decode('utf-8-sig'))))
    return _tsy_cache[y]

def fetch_treasury(col, backfilling):
    years = range(HISTORY_FROM, TODAY.year + 1) if backfilling else \
        ([TODAY.year - 1] if TODAY.month == 1 else []) + [TODAY.year]
    pts = {}
    for y in years:
        for r in treasury_year(y):
            v = (r.get(col) or '').strip()
            if v and v != 'N/A':
                pts[datetime.datetime.strptime(r['Date'], '%m/%d/%Y').date().isoformat()] = float(v)
    if not pts:
        raise RuntimeError(f'no rows for column {col!r}')
    return pts

def fetch_fred(code, backfilling):
    last_err = None
    for attempt in range(3):
        try:
            txt = get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}', timeout=90).decode()
            pts = {r[0]: float(r[1]) for r in list(csv.reader(io.StringIO(txt)))[1:]
                   if len(r) >= 2 and r[1] not in ('', '.') and r[0] >= f'{HISTORY_FROM}'}
            if pts:
                return pts
            last_err = 'no rows parsed'
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(last_err)

def fetch_rate(rt, backfilling):
    routes = {'boe': [('boe-iadb', lambda: fetch_boe(rt['code'], backfilling))],
              'treasury': [('us-treasury', lambda: fetch_treasury(rt['treasury_col'], backfilling)),
                           ('fred', lambda: fetch_fred(rt['code'], backfilling))]}[rt['source']]
    errors = []
    for name, fn in routes:
        try:
            return name, fn(), errors
        except Exception as e:
            errors.append(f'{name}: {type(e).__name__}: {e}')
    return None, None, errors

# ---------- append-only history ----------
def read_hist(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        return list(csv.reader(f))[1:]

def merge(path, header, fresh):
    """fresh: {date: [values..., source]}. Adds dates not yet stored; never rewrites one.
    Returns (rows, added, revisions) where revisions compare the last 10 stored dates."""
    old = read_hist(path)
    have = {r[0]: r for r in old}
    added = sorted(d for d in fresh if d not in have)
    revisions = []
    for r in old[-10:]:
        f = fresh.get(r[0])
        if f and float(r[1]) and abs(float(f[0]) / float(r[1]) - 1) > REVISION_TOL:
            revisions.append(f'{r[0]}: stored {r[1]}, source now {f[0]}')
    if added:
        rows = sorted(old + [[d] + fresh[d] for d in added])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', newline='') as f:
            w = csv.writer(f, lineterminator='\n')
            w.writerow(header)
            w.writerows(rows)
        os.replace(tmp, path)
    else:
        rows = old
    return rows, added, revisions

def missing_weekdays(have, last, days=14):
    """Weekdays in the fortnight up to the last stored date with no row (includes bank holidays)."""
    end = datetime.date.fromisoformat(last)
    span = (end - datetime.timedelta(days=i) for i in range(1, days))
    return sorted(d.isoformat() for d in span if d.weekday() < 5 and d.isoformat() not in have)

def fmt(x):
    return f'{x:.6f}'.rstrip('0').rstrip('.') if x else '0'

def describe(rows, kind):
    """Last value, previous value and change from the stored history."""
    if not rows:
        return {}
    last, prev = rows[-1], rows[-2] if len(rows) > 1 else None
    out = {'first': rows[0][0], 'last_date': last[0], 'last_value': float(last[1]), 'n': len(rows),
           'age_days': (TODAY - datetime.date.fromisoformat(last[0])).days}
    if prev:
        p, l = float(prev[1]), float(last[1])
        out['prev_date'] = prev[0]
        if kind == 'etf':
            out['change_pct'] = round((l + float(last[2])) / p * 100 - 100, 3) if p else None
        else:
            out['change_bp'] = round((l - p) * 100, 1)
    return out

# ---------- run ----------
def main():
    cfg = json.load(open(os.path.join(HERE, 'instruments.json')))
    results = []
    for inst in cfg['etfs']:
        r = {'kind': 'etf', **{k: inst[k] for k in ('layer', 'id', 'name', 'ticker', 'yahoo', 'dist')}, 'flags': []}
        data, errs = fetch_yahoo(inst['yahoo'])
        try:
            if not data:
                raise RuntimeError('; '.join(errs))
            gbp = {d: v for d, v in to_gbp(data).items() if d <= CUTOFF}
            src = {d: data['source'] for d in gbp}
            if inst.get('backfill'):
                pre, note = backfill(inst['backfill'], gbp)
                gbp.update(pre)
                src.update({d: f"{inst['backfill']['yahoo']}/{inst['backfill']['fx']}" for d in pre})
                r['flags'].append(note)
            fresh = {d: [fmt(c), fmt(v), src[d]] for d, (c, v) in gbp.items()}
            rows, added, revs = merge(os.path.join(DATA, 'etf', f"{inst['id']}.csv"),
                                      ['date', 'close_gbp', 'dividend_gbp', 'source'], fresh)
            r.update(status='ok', source=data['source'], currency=data['currency'],
                     added=len(added), revisions=revs, **describe(rows, 'etf'))
            new = [x for x in rows if x[0] in set(added)]
            idx = {x[0]: i for i, x in enumerate(rows)}
            jumps = [x[0] for x in new if idx[x[0]] and float(rows[idx[x[0]] - 1][1])
                     and abs(float(x[1]) / float(rows[idx[x[0]] - 1][1]) - 1) > 0.25]
            if len(added) < len(rows) and jumps:  # on a backfill old splits/glitches are history, not news
                r['flags'].append(f'move over 25% on {", ".join(jumps[:5])} - possible pence/pounds glitch')
                r['status'] = 'check'
            big_div = [x[0] for x in new if float(x[2]) > 0.1 * float(x[1])]
            if big_div:
                r['flags'].append(f'dividend over 10% of price on {", ".join(big_div[:5])} - check units')
                r['status'] = 'check'
            if inst['dist'] and not any(float(x[2]) for x in rows):
                r['flags'].append('distributing but no dividends stored')
            if r['age_days'] > 5:
                r['flags'].append(f"latest price {r['age_days']} days old"); r['status'] = 'check'
            gaps = missing_weekdays({x[0] for x in rows}, rows[-1][0])
            if gaps:  # often a vendor gap (Yahoo leaves an empty close); filled on a later run if the source fills it
                r['flags'].append(f'no price for weekday(s) {", ".join(gaps)}')
            if revs:
                r['flags'].append(f'{len(revs)} stored price(s) differ from source now'); r['status'] = 'check'
        except Exception as e:
            r.update(status='failed', errors=[f'{type(e).__name__}: {e}'] + errs)
        results.append(r)
        print(f"{r['status']:7} {inst['ticker']:5} +{r.get('added', 0):<5} {inst['name']}",
              r['flags'] or r.get('errors', ''), flush=True)
        time.sleep(0.5)

    for rt in cfg['rates']:
        r = {'kind': 'rate', **{k: rt[k] for k in ('layer', 'id', 'name', 'source', 'code')}, 'flags': []}
        path = os.path.join(DATA, 'rates', f"{rt['id']}.csv")
        name, pts, errs = fetch_rate(rt, backfilling=not os.path.exists(path))
        if pts:
            rows, added, revs = merge(path, ['date', 'value', 'source'], {d: [fmt(v), name] for d, v in pts.items()})
            r.update(status='ok', source=name, added=len(added), revisions=revs, **describe(rows, 'rate'))
            if errs: r['flags'].append('fallback used: ' + '; '.join(errs)[:150])
            if r['age_days'] > 7: r['flags'].append(f"latest {r['age_days']} days old"); r['status'] = 'check'
            if revs: r['flags'].append(f'{len(revs)} stored value(s) differ from source now'); r['status'] = 'check'
        else:
            r.update(status='failed', errors=errs)
        results.append(r)
        print(f"{r['status']:7} {rt['code']:8} +{r.get('added', 0):<5} {rt['name']}",
              r['flags'] or r.get('errors', ''), flush=True)

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    snap = {'date': TODAY.isoformat(), 'run': stamp, 'etf_cutoff': CUTOFF, 'results': results}
    os.makedirs(os.path.join(DATA, 'daily'), exist_ok=True)
    for p in (os.path.join(DATA, 'daily', f'{TODAY}.json'), os.path.join(DATA, 'latest.json')):
        json.dump(snap, open(p, 'w'), indent=1)

    n = {s: sum(r['status'] == s for r in results) for s in ('ok', 'check', 'failed')}
    lines = [f'# Market Picture daily build - {TODAY} ({stamp})', '',
             f"**{n['ok']} ok, {n['check']} to check, {n['failed']} failed, of {len(results)} series.** "
             f'ETF prices up to {CUTOFF}.', '',
             '| Layer | Series | Ticker/code | Status | Source | Last date | Last | Change | Added | Stored from | Notes |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in results:
        chg = (f"{r['change_pct']:+.2f}%" if r.get('change_pct') is not None else '') if r['kind'] == 'etf' \
            else (f"{r['change_bp']:+.1f}bp" if 'change_bp' in r else '')
        last = (f"£{r['last_value']:.4f}" if r['kind'] == 'etf' else f"{r['last_value']:.2f}%") if 'last_value' in r else ''
        note = '; '.join(r['flags'] + r.get('errors', [])).replace('|', '/')[:220]
        lines.append(f"| {r['layer']} | {r['name']} | {r.get('ticker') or r.get('code')} | {r['status']} | "
                     f"{r.get('source', '')} | {r.get('last_date', '')} | {last} | {chg} | {r.get('added', '')} | "
                     f"{r.get('first', '')} | {note} |")
    md = '\n'.join(lines) + '\n'
    open(os.path.join(DATA, 'report.md'), 'w').write(md)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        open(os.environ['GITHUB_STEP_SUMMARY'], 'a').write(md)
    if os.environ.get('GITHUB_OUTPUT'):
        open(os.environ['GITHUB_OUTPUT'], 'a').write(f"failed={n['failed']}\ncheck={n['check']}\n")
    print(f"SUMMARY {n['ok']} ok, {n['check']} to check, {n['failed']} failed, of {len(results)}")

if __name__ == '__main__':
    main()
