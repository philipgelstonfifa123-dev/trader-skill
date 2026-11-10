#!/usr/bin/env python3
"""Backtest of copying 13F filers: who would have been worth copying.

For every filer in filers.json (or the CIKs you name): the day after each
13F-HR is filed, buy its top N stocks at the copy weights, hold until the day
after the next filing, repeat. Compared with holding SPY over the same days.
Prices are Yahoo daily adjusted closes (dividends included).

  python3 backtest.py                    # all bundled filers, 5 years, top 10
  python3 backtest.py 1067983 --years 3 --top 5

Past results only. No trading costs or taxes. Education, not advice.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar13f as e13  # noqa: E402

CHART = "https://query2.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d"


def parse_prices(doc):
    """{date: adjusted close} from a Yahoo chart JSON."""
    res = (doc.get("chart", {}).get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return {}
    ind = res["indicators"]
    closes = (ind.get("adjclose") or ind["quote"])[0]
    closes = closes.get("adjclose") or closes.get("close")
    return {datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d"): c
            for t, c in zip(res["timestamp"], closes) if c}


def simulate(rebalances, prices, days):
    """Daily value of $1 that follows the rebalances.

    rebalances: [(filed_date, {ticker: weight})], oldest first. Each is bought
    at the close of the first day after filed_date. days: sorted trading days.
    Returns ([(day, value)], share of weight that had no price and was dropped)."""
    starts = []
    for filed, weights in rebalances:
        later = [i for i, d in enumerate(days) if d > filed]
        if later and (not starts or later[0] > starts[-1][0]):
            starts.append((later[0], weights))
    curve, value, dropped = [], 1.0, []
    for n, (i0, weights) in enumerate(starts):
        i1 = starts[n + 1][0] if n + 1 < len(starts) else len(days) - 1
        held = {t: w for t, w in weights.items() if prices.get(t, {}).get(days[i0])}
        total, base = sum(weights.values()), sum(held.values())
        dropped.append(1 - base / total if total else 1.0)
        last = {t: prices[t][days[i0]] for t in held}
        for i in range(i0, i1 + 1):
            for t in held:  # a missing day (halt, delisting) keeps the last price
                last[t] = prices[t].get(days[i]) or last[t]
            v = value * sum(w / base * last[t] / prices[t][days[i0]]
                            for t, w in held.items()) if base else value
            if i > i0 or not curve:
                curve.append((days[i], v))
        value = curve[-1][1] if curve else value
    return curve, (sum(dropped) / len(dropped) if dropped else 1.0)


def stats(curve):
    """Total return, yearly return and worst peak-to-trough drop, in %."""
    first, last = curve[0], curve[-1]
    years = (datetime.date.fromisoformat(last[0]) - datetime.date.fromisoformat(first[0])).days / 365.25
    total = last[1] / first[1]
    peak, dd = first[1], 0.0
    for _, v in curve:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    return {"total": 100 * (total - 1), "years": years, "drawdown": 100 * dd,
            "yearly": 100 * (total ** (1 / years) - 1) if years > 0 else 0.0}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cik", nargs="*", help="CIKs to test (default: every filer in filers.json)")
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--user-agent")
    ap.add_argument("--json", help="also write the full results here")
    a = ap.parse_args(argv)
    ua = e13.user_agent(a.user_agent)
    if not ua:
        sys.exit(e13.UA_HELP)
    sec = e13.Fetcher(ua)
    index = e13.ticker_index(sec)
    filers = [f for f in e13.load_filers() if not a.cik or
              e13.cik10(f["cik"]) in {e13.cik10(c) for c in a.cik}]
    filers += [{"cik": c, "name": "CIK " + c} for c in a.cik
               if e13.cik10(c) not in {e13.cik10(f["cik"]) for f in filers}]
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=round(365.25 * a.years))).isoformat()
    p1 = int(time.mktime((today - datetime.timedelta(days=round(365.25 * a.years) + 10)).timetuple()))
    p2 = int(time.time())
    cache = {}
    # Yahoo answers 429 to the default client name; no contact details go to it.
    yahoo = e13.Fetcher("Mozilla/5.0 (compatible; trader-skill/1.0)", pause=0.2)

    def prices(ticker):
        if ticker not in cache:
            sym = urllib.parse.quote(ticker.replace(".", "-"))
            try:
                cache[ticker] = parse_prices(yahoo.get_json(CHART % (sym, p1, p2), sec=False))
            except e13.EdgarError:
                cache[ticker] = {}
        return cache[ticker]

    spy = prices("SPY")
    days = sorted(spy)
    if not days:
        sys.exit("backtest.py: no SPY prices from Yahoo, cannot compare")
    rows = []
    for f in filers:
        try:
            _, _, filings = e13.load_quarter(sec, f["cik"])
            quarters = sorted({q["period"] for q in filings
                               if q["form"] == "13F-HR" and q["filed"] >= since})
            rebalances = []
            for period in quarters:
                try:
                    meta, table, _ = e13.load_quarter(sec, f["cik"], period=period)
                    positions, _ = e13.build(meta, table, index, combine=True)
                    weights, _ = e13.copy_weights(positions, top=a.top)
                except (e13.EdgarError, KeyError, ValueError) as exc:
                    print("  %s %s skipped: %s" % (f["name"], period, str(exc)[:80]), file=sys.stderr)
                    continue
                rebalances.append((meta["filed"], {w["ticker"]: w["weight"] for w in weights}))
            rebalances.sort()
            for _, w in rebalances:
                for t in w:
                    prices(t)
            curve, dropped = simulate(rebalances, cache, days)
            if len(curve) < 2:
                raise e13.EdgarError("not enough filings or prices")
            s = stats(curve)
            b = stats([(d, spy[d]) for d, _ in curve])
            rows.append({"name": f["name"], "person": f.get("person", ""), "cik": e13.cik10(f["cik"]),
                         "from": curve[0][0], "to": curve[-1][0], "filings": len(rebalances),
                         "last_filed": rebalances[-1][0], "latest": rebalances[-1][1],
                         "dropped_pct": round(100 * dropped, 1), "spy_yearly": b["yearly"],
                         "edge": s["yearly"] - b["yearly"], "spy_drawdown": b["drawdown"], **s})
            print("  done %s" % f["name"], file=sys.stderr)
        except e13.EdgarError as exc:
            print("  %s skipped: %s" % (f["name"], str(exc)[:100]), file=sys.stderr)
    rows.sort(key=lambda r: r["edge"], reverse=True)
    print("%-38s %5s %8s %8s %8s %8s %6s" % ("filer", "yrs", "per yr", "SPY", "edge", "worst", "noprc"))
    for r in rows:
        print("%-38s %5.1f %7.1f%% %7.1f%% %+7.1f%% %7.1f%% %5.1f%%" % (
            r["name"][:38], r["years"], r["yearly"], r["spy_yearly"], r["edge"],
            r["drawdown"], r["dropped_pct"]))
    print("\nper yr = yearly return of the copy. edge = per yr minus SPY over the same days. "
          "worst = biggest drop from a peak. noprc = average weight with no Yahoo price "
          "(left out). Top %d holdings, bought the day after each filing. Past results, "
          "no costs or taxes, not advice." % a.top)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
