#!/usr/bin/env python3
"""Minato Wealth Market Picture - data source test (v1, 26 Oct 2026).

Fetches every series in instruments.json once and reports what came back.
Nothing is published. Writes:
  test-results/latest.md    human-readable report (also sent to the GitHub job summary)
  test-results/latest.json  machine-readable results
Exit code is always 0 unless the script itself breaks: the point is the report.

ETFs: Yahoo Finance chart API (standard library), falling back to the yfinance
package if it is installed. Rates: Bank of England IADB CSV and FRED CSV.
"""
import csv, datetime, io, json, os, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'test-results')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept': 'application/json,text/csv,*/*'}
TODAY = datetime.date.today()

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def day(ts):
    return datetime.datetime.utcfromtimestamp(ts).date().isoformat()

# ---------- ETFs ----------
def yahoo_std(symbol):
    """Yahoo v8 chart API, full history, daily, with dividend events."""
    last_err = None
    for attempt in range(4):
        host = 'query1' if attempt % 2 == 0 else 'query2'
        url = (f'https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}'
               '?range=max&interval=1d&events=div&includeAdjustedClose=true')
        try:
            j = json.loads(get(url))
            res = j['chart']['result'][0]
            meta = res.get('meta', {})
            ts = res.get('timestamp') or []
            q = res['indicators']['quote'][0]
            closes = q.get('close') or []
            adj = (res['indicators'].get('adjclose') or [{}])[0].get('adjclose') or []
            pts = [(day(t), c) for t, c in zip(ts, closes) if c is not None]
            divs = (res.get('events') or {}).get('dividends') or {}
            return {'source': 'yahoo-v8', 'currency': meta.get('currency'),
                    'exchange': meta.get('exchangeName'), 'long_name': meta.get('longName') or meta.get('shortName'),
                    'points': pts, 'adj_available': any(a is not None for a in adj),
                    'dividends': len(divs)}
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last_err)

def yahoo_yf(symbol):
    import yfinance as yf  # optional
    t = yf.Ticker(symbol)
    h = t.history(period='max', interval='1d', auto_adjust=False, actions=True)
    if h is None or h.empty:
        raise RuntimeError('yfinance returned no rows')
    pts = [(i.date().isoformat(), float(c)) for i, c in zip(h.index, h['Close']) if c == c]
    cur = None
    try: cur = t.fast_info.get('currency')
    except Exception: pass
    divs = int((h['Dividends'] > 0).sum()) if 'Dividends' in h else 0
    return {'source': 'yfinance', 'currency': cur, 'exchange': None, 'long_name': None,
            'points': pts, 'adj_available': 'Adj Close' in h, 'dividends': divs}

def fetch_etf(inst):
    errors = []
    for fn in (yahoo_std, yahoo_yf):
        try:
            return fn(inst['yahoo']), errors
        except Exception as e:
            errors.append(f'{fn.__name__}: {e}')
    return None, errors

# ---------- Rates ----------
def fetch_boe(code):
    url = ('https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes'
           f'&Datefrom=01/Jan/2016&Dateto=now&SeriesCodes={code}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N')
    txt = get(url).decode('utf-8', 'replace')
    rows = list(csv.reader(io.StringIO(txt)))
    pts = []
    for r in rows[1:]:
        if len(r) >= 2 and r[1].strip():
            try:
                d = datetime.datetime.strptime(r[0].strip(), '%d %b %Y').date().isoformat()
                pts.append((d, float(r[1])))
            except ValueError:
                pass
    if not pts:
        raise RuntimeError('no rows parsed; first line: ' + (txt[:120].replace('\n', ' ')))
    return {'source': 'boe-iadb', 'header': rows[0] if rows else None, 'points': pts}

def fetch_fred(code):
    txt = get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}').decode()
    pts = []
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        if len(r) >= 2 and r[1] not in ('', '.'):
            pts.append((r[0], float(r[1])))
    if not pts:
        raise RuntimeError('no rows parsed')
    return {'source': 'fred', 'points': pts}

# ---------- run ----------
def summarise(pts):
    pts = sorted(pts)
    last_d = pts[-1][0]
    age = (TODAY - datetime.date.fromisoformat(last_d)).days
    return {'first': pts[0][0], 'last': last_d, 'n': len(pts), 'last_value': pts[-1][1], 'age_days': age}

def main():
    cfg = json.load(open(os.path.join(HERE, 'instruments.json')))
    results = []
    for inst in cfg['etfs']:
        data, errs = fetch_etf(inst)
        r = {'kind': 'etf', **{k: inst[k] for k in ('layer', 'id', 'name', 'ticker', 'yahoo', 'dist')}}
        if data and data['points']:
            s = summarise(data['points'])
            cur = data.get('currency')
            r.update(status='ok', source=data['source'], currency=cur, exchange=data.get('exchange'),
                     long_name=data.get('long_name'), dividends=data['dividends'], **s)
            flags = []
            if cur not in ('GBp', 'GBP', 'GBX'): flags.append(f'currency {cur} not sterling')
            if s['age_days'] > 5: flags.append(f'latest price {s["age_days"]} days old')
            if s['first'] > '2019-01-01': flags.append(f'history only from {s["first"]}')
            if inst['dist'] and data['dividends'] == 0: flags.append('distributing but no dividends reported')
            r['flags'] = flags
            if flags and any('currency' in f or 'days old' in f for f in flags): r['status'] = 'check'
        else:
            r.update(status='failed', errors=errs, flags=[])
        results.append(r)
        print(f"{r['status']:7} {inst['ticker']:5} {inst['name']}", r.get('flags') or r.get('errors', ''), flush=True)
        time.sleep(1.0)  # be gentle with Yahoo
    for rt in cfg['rates']:
        r = {'kind': 'rate', **{k: rt[k] for k in ('layer', 'id', 'name', 'source', 'code')}}
        try:
            data = fetch_boe(rt['code']) if rt['source'] == 'boe' else fetch_fred(rt['code'])
            s = summarise(data['points'])
            r.update(status='ok', header=data.get('header'), **s)
            r['flags'] = [f'latest {s["age_days"]} days old'] if s['age_days'] > 7 else []
        except Exception as e:
            r.update(status='failed', errors=[f'{type(e).__name__}: {e}'], flags=[])
        results.append(r)
        print(f"{r['status']:7} {rt['code']:8} {rt['name']}", r.get('flags') or r.get('errors', ''), flush=True)

    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    json.dump({'run': stamp, 'results': results}, open(os.path.join(OUT, 'latest.json'), 'w'), indent=1)

    ok = sum(r['status'] == 'ok' for r in results)
    chk = sum(r['status'] == 'check' for r in results)
    bad = sum(r['status'] == 'failed' for r in results)
    lines = [f'# Market Picture data test - {stamp}', '',
             f'**{ok} ok, {chk} to check, {bad} failed, of {len(results)} series.**', '',
             '| Layer | Series | Ticker/code | Status | Source | Currency | From | To | Points | Notes |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for r in results:
        note = '; '.join(r.get('flags') or []) or '; '.join(r.get('errors') or [])
        lines.append('| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            r['layer'], r['name'], r.get('ticker') or r.get('code'), r['status'], r.get('source', ''),
            r.get('currency', '') or '', r.get('first', ''), r.get('last', ''), r.get('n', ''),
            note.replace('|', '/')[:200]))
    md = '\n'.join(lines) + '\n'
    open(os.path.join(OUT, 'latest.md'), 'w').write(md)
    summ = os.environ.get('GITHUB_STEP_SUMMARY')
    if summ:
        open(summ, 'a').write(md)
    print(f'SUMMARY {ok} ok, {chk} to check, {bad} failed, of {len(results)}')

if __name__ == '__main__':
    main()
