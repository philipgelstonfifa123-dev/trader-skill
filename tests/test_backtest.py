import unittest

from _load import load

bt = load("trader-copycat", "backtest")

DAYS = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
PRICES = {
    "AAA": dict(zip(DAYS, [10, 10, 20, 20, 20])),
    "BBB": dict(zip(DAYS, [5, 5, 5, 5, 10])),
    "GONE": {},
}


class Backtest(unittest.TestCase):
    def test_buys_day_after_filing_and_compounds(self):
        # Filed Jan 2: buy AAA at the Jan 5 close (10), it doubles. Filed Jan 6:
        # switch to BBB at the Jan 7 close (5), it doubles again. $1 -> $4.
        curve, dropped = bt.simulate([("2026-01-02", {"AAA": 100.0}),
                                      ("2026-01-06", {"BBB": 100.0})], PRICES, DAYS)
        self.assertEqual(curve[0], ("2026-01-05", 1.0))
        self.assertEqual([round(v, 6) for _, v in curve], [1.0, 2.0, 2.0, 4.0])
        self.assertEqual(dropped, 0.0)

    def test_ticker_without_price_is_dropped_and_reported(self):
        curve, dropped = bt.simulate([("2026-01-02", {"AAA": 50.0, "GONE": 50.0})], PRICES, DAYS)
        self.assertAlmostEqual(curve[-1][1], 2.0)
        self.assertAlmostEqual(dropped, 0.5)

    def test_stats(self):
        s = bt.stats([("2025-01-01", 1.0), ("2025-07-01", 2.0), ("2025-10-01", 1.0),
                      ("2026-01-01", 1.21)])
        self.assertAlmostEqual(s["total"], 21.0)
        self.assertAlmostEqual(s["drawdown"], -50.0)
        self.assertAlmostEqual(s["yearly"], 21.0, places=0)

    def test_parse_prices_uses_adjusted_close(self):
        doc = {"chart": {"result": [{"timestamp": [1767362400, 1767621600],
                                     "indicators": {"quote": [{"close": [10.0, None]}],
                                                    "adjclose": [{"adjclose": [9.5, None]}]}}]}}
        self.assertEqual(bt.parse_prices(doc), {"2026-01-02": 9.5})


if __name__ == "__main__":
    unittest.main()
