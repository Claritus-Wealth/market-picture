#!/usr/bin/env python3
"""Minato Wealth Market Picture - daily view (stage 3, 27 Oct 2026).

Reads Minato's own stored history (data/etf, data/rates; written by build_daily.py) and
builds the daily view: returns over 1d, 1w, 1m, 3m, YTD and 1y for every series, by layer,
all measured to one "as at" date. Fetches nothing; standard library only.

ETFs: total return in sterling, from the stored close plus dividends (dividend reinvested on
its ex-date: TR_t = TR_t-1 x (close_t + dividend_t) / close_t-1), so a distributing line is
comparable with an accumulating one. Vendor "adjusted close" is never used.
Rates: level, and the change in basis points over the same periods.

Bad prints: Yahoo occasionally stores a London close in the wrong currency for a day (e.g.
24 Oct 2025, about 1.34x on most lines - the GBP/USD rate). A close more than 20% away from
the median of the 11 days around it, confirmed by at least two later days, is left out of the
index and listed. A jump of that size in the last two days cannot be confirmed yet: that line's
cells are left blank and it is marked "check". The stored history is never changed.

Writes:
  view/view.json   every figure on the page, with the dates each was measured between
  view/index.html  the page: view_template.html with the data injected at /*__DATA__*/

Usage: build_view.py [--as-of YYYY-MM-DD]   (default: the latest common ETF date)
Not published anywhere: the page is committed to the repository only.
"""
import argparse, bisect, csv, datetime, json, os, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
OUT = os.path.join(HERE, 'view')
HORIZONS = ['1d', '1w', '1m', '3m', 'ytd', '1y']
BAD_PRINT = 0.20   # distance from the local median that marks a close as a bad print
WINDOW = 5         # days either side for that median
STALE_DAYS = 3     # a line whose last close is this many days before the as-at date is flagged

def read(path):
    with open(path, newline='') as f:
        return list(csv.reader(f))[1:]

def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(d.day, last))

def starts(as_of):
    """Calendar start of each period (the base is the last close on or before it); 1d is the previous close."""
    return {'1w': as_of - datetime.timedelta(days=7), '1m': add_months(as_of, -1),
            '3m': add_months(as_of, -3), 'ytd': datetime.date(as_of.year - 1, 12, 31),
            '1y': add_months(as_of, -12)}

def bad_prints(dates, closes):
    """Indices of confirmed bad prints, and of unconfirmed jumps in the last two rows."""
    bad, unconfirmed = [], []
    n = len(closes)
    for i in range(n):
        lo, hi = max(0, i - WINDOW), min(n, i + WINDOW + 1)
        med = statistics.median(closes[lo:hi])
        if med and abs(closes[i] / med - 1) > BAD_PRINT:
            (unconfirmed if i >= n - 2 else bad).append(i)
    return bad, unconfirmed

def tr_index(rows):
    """rows: [(date, close, dividend)] in date order, bad prints removed -> [(date, index)]."""
    out, idx = [], 1.0
    for i, (d, c, v) in enumerate(rows):
        if i:
            idx *= (c + v) / rows[i - 1][1]
        out.append((d, idx))
    return out

def on_or_before(series, d):
    """Last (date, value) on or before d, or None."""
    dates = [x[0] for x in series]
    i = bisect.bisect_right(dates, d.isoformat()) - 1
    return series[i] if i >= 0 else None

def measure(series, as_of, kind):
    """series: [(date, value)] in date order. Returns end point and each period's change."""
    end = on_or_before(series, as_of)
    if not end:
        return None, {}
    k = series.index(end)
    bases = {'1d': series[k - 1] if k else None}
    for h, s in starts(as_of).items():
        b = on_or_before(series, s)
        # no number unless the history really reaches back to the period start
        bases[h] = b if b and b[0] < end[0] and series[0][0] <= s.isoformat() else None
    cells = {}
    for h in HORIZONS:
        b = bases[h]
        if not b:
            cells[h] = None
        elif kind == 'etf':
            cells[h] = {'v': round((end[1] / b[1] - 1) * 100, 2), 'from': b[0]}
        else:
            cells[h] = {'v': round((end[1] - b[1]) * 100, 1), 'from': b[0]}
    return end, cells

def latest_common(etf_dates, as_of=None):
    """The latest date held by at least half the ETF lines (on or before as_of if given)."""
    cut = as_of.isoformat() if as_of else '9999'
    counts = {}
    for ds in etf_dates:
        for d in ds[-30:] if not as_of else [x for x in ds if x <= cut][-30:]:
            counts[d] = counts.get(d, 0) + 1
    ok = [d for d, c in counts.items() if c >= len(etf_dates) / 2]
    return datetime.date.fromisoformat(max(ok))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--as-of', help='build the view as at this date (YYYY-MM-DD)')
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, 'instruments.json')))
    hist = {e['id']: [(r[0], float(r[1]), float(r[2])) for r in read(os.path.join(DATA, 'etf', f"{e['id']}.csv"))]
            for e in cfg['etfs']}
    as_of = latest_common([[r[0] for r in h] for h in hist.values()],
                          datetime.date.fromisoformat(args.as_of) if args.as_of else None)
    if args.as_of:  # look only at what was stored up to that date
        hist = {k: [r for r in h if r[0] <= as_of.isoformat()] for k, h in hist.items()}

    rows, excluded = [], []
    for e in cfg['etfs']:
        h = hist[e['id']]
        bad, unconf = bad_prints([r[0] for r in h], [r[1] for r in h])
        if bad:
            excluded.append({'id': e['id'], 'ticker': e['ticker'], 'dates': [h[i][0] for i in bad]})
        clean = [r for i, r in enumerate(h) if i not in set(bad)]
        end, cells = measure(tr_index(clean), as_of, 'etf')
        r = {'kind': 'etf', 'layer': e['layer'], 'id': e['id'], 'name': e['name'], 'ticker': e['ticker'],
             'dist': e['dist'], 'flags': [], 'status': 'ok'}
        if end:
            p = on_or_before([(x[0], x[1]) for x in clean], as_of)
            r.update(last_date=end[0], level=round(p[1], 4))
            if (as_of - datetime.date.fromisoformat(end[0])).days >= STALE_DAYS:
                r['flags'].append(f'last close {end[0]}'); r['status'] = 'check'
        if unconf:
            r['flags'].append(f'unconfirmed jump over {BAD_PRINT:.0%} on {", ".join(h[i][0] for i in unconf)}'
                              ' - figures held back until later closes confirm or correct it')
            r['status'] = 'check'
            cells = {k: None for k in HORIZONS}
        r['cells'] = cells
        rows.append(r)

    for rt in cfg['rates']:
        s = [(x[0], float(x[1])) for x in read(os.path.join(DATA, 'rates', f"{rt['id']}.csv"))]
        if args.as_of:
            s = [x for x in s if x[0] <= as_of.isoformat()]
        end, cells = measure(s, as_of, 'rate')
        r = {'kind': 'rate', 'layer': rt['layer'], 'id': rt['id'], 'name': rt['name'], 'ticker': rt['code'],
             'flags': [], 'status': 'ok', 'cells': cells}
        if end:
            r.update(last_date=end[0], level=end[1])
            if (as_of - datetime.date.fromisoformat(end[0])).days >= STALE_DAYS + 2:  # publication lag
                r['flags'].append(f'last value {end[0]}'); r['status'] = 'check'
        rows.append(r)

    by_id = {r['id']: r for r in rows}
    pairs = []
    for p in cfg.get('relationships', []):
        a, b = by_id[p['a']], by_id[p['b']]
        cells = {}
        for h in HORIZONS:
            x, y = a['cells'].get(h), b['cells'].get(h)
            if not x or not y:
                cells[h] = None
            elif a['kind'] == 'etf':  # relative total return
                cells[h] = {'v': round(((1 + x['v'] / 100) / (1 + y['v'] / 100) - 1) * 100, 2), 'from': x['from']}
            else:  # change in the spread, basis points
                cells[h] = {'v': round(x['v'] - y['v'], 1), 'from': x['from']}
        q = {'kind': a['kind'], 'name': p['name'], 'a': a['ticker'], 'b': b['ticker'], 'cells': cells,
             'flags': [f"{t['ticker']}: {f}" for t in (a, b) for f in t['flags']]}
        if a['kind'] == 'rate' and 'level' in a and 'level' in b:
            q['level'] = round(a['level'] - b['level'], 3)
        pairs.append(q)

    snap_path = os.path.join(DATA, 'latest.json')
    build = json.load(open(snap_path)) if os.path.exists(snap_path) else {}
    # weekdays after the as-at date that the build has passed but no London closes are stored for
    cutoff = datetime.date.fromisoformat(build.get('etf_cutoff') or as_of.isoformat())
    pending = [(as_of + datetime.timedelta(days=i)).isoformat() for i in range(1, (cutoff - as_of).days + 1)
               if (as_of + datetime.timedelta(days=i)).weekday() < 5] if not args.as_of else []
    out = {'as_of': as_of.isoformat(), 'built': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
           'build_run': build.get('run'), 'horizons': HORIZONS, 'pending_days': pending,
           'starts': {h: s.isoformat() for h, s in starts(as_of).items()},
           'rows': rows, 'relationships': pairs, 'bad_prints': excluded,
           'counts': {s: sum(r['status'] == s for r in rows) for s in ('ok', 'check')}}
    os.makedirs(OUT, exist_ok=True)
    json.dump(out, open(os.path.join(OUT, 'view.json'), 'w'), indent=1)
    tpl = open(os.path.join(HERE, 'view_template.html')).read()
    if '/*__DATA__*/' not in tpl:
        raise SystemExit('view_template.html is missing the /*__DATA__*/ marker')
    open(os.path.join(OUT, 'index.html'), 'w').write(tpl.replace('/*__DATA__*/', json.dumps(out, separators=(',', ':'))))
    recent = sum(1 for b in excluded for d in b['dates'] if d >= starts(as_of)['1y'].isoformat())
    print(f"VIEW as at {as_of}: {out['counts']['ok']} ok, {out['counts']['check']} to check, of {len(rows)}; "
          f"bad prints left out: {sum(len(b['dates']) for b in excluded)} ({recent} inside the last year)")

if __name__ == '__main__':
    main()
