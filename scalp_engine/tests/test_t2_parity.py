"""Parity with the live bot on every live-journal day committed in git from 2026-10-20 to the start of T2 (ADR 0017
item 6): the replay in parity mode issues exactly the journaled tickets, with identical prices, R and shares, and
the same re-ranked watchlist with identical fields (floating-point ATR differences below 1e-9 relative are the
only tolerated difference). Needs the T1/T2 store; skipped without it."""
import json
import os
import unittest
from pathlib import Path

# data/intraday_state.json at a83748e (2026-10-20 15:30Z), embedded so no data file is tracked
JOURNAL_2026_10_20 = {'day': '2026-10-20',
 'watch': [{'symbol': 'BRZE',
            'gap': 0.5158,
            'pre_vol': 4810444,
            'pre_rel': 2.4325,
            'open': 45.535,
            'atr': 1.514928571428572,
            'prev_close': 30.04,
            'adv': 1977542.5,
            'open_relvol': 58.31914125611176},
           {'symbol': 'RCUS',
            'gap': -0.0244,
            'pre_vol': 256657,
            'pre_rel': 0.1739,
            'open': 21.6,
            'atr': 1.1449999999999998,
            'prev_close': 22.14,
            'adv': 1475738.5,
            'open_relvol': 23.07810468710664},
           {'symbol': 'HAS',
            'gap': 0.0436,
            'pre_vol': 316918,
            'pre_rel': 0.1681,
            'open': 102.78,
            'atr': 2.565000000000001,
            'prev_close': 98.49,
            'adv': 1885013.55,
            'open_relvol': 23.005005352403206},
           {'symbol': 'KVYO',
            'gap': 0.0816,
            'pre_vol': 1410143,
            'pre_rel': 0.2583,
            'open': 18.95,
            'atr': 0.9227214285714288,
            'prev_close': 17.52,
            'adv': 5460339.0,
            'open_relvol': 14.917952973407768},
           {'symbol': 'GM',
            'gap': -0.0662,
            'pre_vol': 1457073,
            'pre_rel': 0.2129,
            'open': 77.275,
            'atr': 2.0393000000000003,
            'prev_close': 82.75,
            'adv': 6845491.65,
            'open_relvol': 9.592511174854366},
           {'symbol': 'GE',
            'gap': -0.0428,
            'pre_vol': 353043,
            'pre_rel': 0.0871,
            'open': 297.96,
            'atr': 6.534764285714293,
            'prev_close': 311.28,
            'adv': 4052866.7,
            'open_relvol': 4.628872023939692},
           {'symbol': 'VICR',
            'gap': -0.0005,
            'pre_vol': 158488,
            'pre_rel': 0.1421,
            'open': 190.58,
            'atr': 22.873307142857136,
            'prev_close': 190.68,
            'adv': 1115446.0,
            'open_relvol': 3.272203904156376},
           {'symbol': 'RIO',
            'gap': -0.0207,
            'pre_vol': 279285,
            'pre_rel': 0.1363,
            'open': 87.645,
            'atr': 1.942385714285712,
            'prev_close': 89.5,
            'adv': 2049070.55,
            'open_relvol': 2.0902802074222167},
           {'symbol': 'CRWV',
            'gap': -0.0303,
            'pre_vol': 4160737,
            'pre_rel': 0.0977,
            'open': 59.27,
            'atr': 5.97,
            'prev_close': 61.12,
            'adv': 42588224.15,
            'open_relvol': 1.951185672976324},
           {'symbol': 'INFQ',
            'gap': -0.028,
            'pre_vol': 349132,
            'pre_rel': 0.0468,
            'open': 8.505,
            'atr': 0.704635714285714,
            'prev_close': 8.75,
            'adv': 7464473.5,
            'open_relvol': 1.927342138877873},
           {'symbol': 'CRM',
            'gap': -0.0276,
            'pre_vol': 384117,
            'pre_rel': 0.0436,
            'open': 235.75,
            'atr': 8.606378571428577,
            'prev_close': 242.45,
            'adv': 8819364.3,
            'open_relvol': 1.4567512111792675}],
 'sent': {'SCO:orb5': '09:40', 'UGL:orb15': '09:50', 'OKLO:orb15': '09:50', 'KVYO:vwap_hammer': '10:00'},
 'journal': [{'ok': True,
              'symbol': 'SCO',
              'setup': 'orb5',
              'note': 'פריצת טווח הפתיחה של 5 דקות',
              'score': 3.2376,
              'entry_stop': 16.41,
              'limit': 16.44,
              'stop_loss': 16.34,
              'target': 16.54,
              'R': 0.07,
              'r_floored': False,
              'shares': 6,
              'risk': 0.4,
              'time_exit': '15:55',
              'atr': 0.6661785714285716,
              'gap': -0.0163,
              'pre_rel': 0.1188,
              'open_relvol': None,
              'at': '09:40',
              'last': 16.39,
              'vwap': 16.34040920982143},
             {'ok': True,
              'symbol': 'UGL',
              'setup': 'orb15',
              'note': 'פריצת טווח הפתיחה של 15 דקות',
              'score': 3.2448,
              'entry_stop': 44.99,
              'limit': 45.05,
              'stop_loss': 44.86,
              'target': 45.26,
              'R': 0.13,
              'r_floored': True,
              'shares': 2,
              'risk': 0.27,
              'time_exit': '15:55',
              'atr': 1.1822357142857152,
              'gap': -0.0099,
              'pre_rel': 0.1224,
              'open_relvol': None,
              'at': '09:50',
              'last': 44.9,
              'vwap': 44.85038518446912},
             {'ok': True,
              'symbol': 'OKLO',
              'setup': 'orb15',
              'note': 'פריצת טווח הפתיחה של 15 דקות',
              'score': 3.0966,
              'entry_stop': 27.2,
              'limit': 27.31,
              'stop_loss': 26.75,
              'target': 28.11,
              'R': 0.46,
              'r_floored': False,
              'shares': 3,
              'risk': 1.37,
              'time_exit': '15:55',
              'atr': 2.15665,
              'gap': -0.0296,
              'pre_rel': 0.0483,
              'open_relvol': None,
              'at': '09:50',
              'last': 27.14,
              'vwap': 26.883545332964925},
             {'ok': True,
              'symbol': 'KVYO',
              'setup': 'vwap_hammer',
              'note': 'פטיש על VWAP',
              'score': 3.5,
              'entry_stop': 19.43,
              'limit': 19.48,
              'stop_loss': 19.3,
              'target': 19.7,
              'R': 0.14,
              'r_floored': False,
              'shares': 5,
              'risk': 0.68,
              'time_exit': '15:55',
              'atr': 0.9227214285714288,
              'gap': 0.0816,
              'pre_rel': 0.2583,
              'open_relvol': 14.917952973407768,
              'at': '10:00',
              'last': 19.4,
              'vwap': 19.29439115605316}],
 'stats': None}
JOURNALS = {"2026-10-20": JOURNAL_2026_10_20}
STORE = Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")


def close(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a))
    return a == b


@unittest.skipUnless((STORE / "derived" / "t1" / "watch").exists(), "needs the T1/T2 store")
class LiveParity(unittest.TestCase):
    def test_every_journal_day(self):
        import datetime as dt
        from scalp_engine import t1_replay as P
        import tempfile
        for day, js in JOURNALS.items():
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "journal.json"
                path.write_text(json.dumps(js), encoding="utf-8")
                out = P.parity(str(path), dt.date.fromisoformat(day))
            self.assertEqual(out["matched"], out["journaled"], day)
            self.assertEqual(out["replay_only"], [], day)
            for w in out["watch"]:
                self.assertTrue(w["live"] and w["replay"], (day, w["symbol"]))
                for f, (a, b) in w["fields"].items():
                    self.assertTrue(close(a, b), (day, w["symbol"], f, a, b))

    def test_fixture_is_the_committed_journal(self):
        js = JOURNALS["2026-10-20"]
        self.assertEqual((js["day"], len(js["journal"]), len(js["watch"])), ("2026-10-20", 4, 11))


if __name__ == "__main__":
    unittest.main()
