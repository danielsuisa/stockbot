"""I2 engine inputs (ADR 0010 section 1): the daily universe-(b) table and the signed event set. Prior-close data only;
no outcome is read or computed here.

    python -m scalp_engine.insider_inputs universe   # writes universe_b_daily
    python -m scalp_engine.insider_inputs sign       # writes derived/i2_event_set.signed.json
"""
import datetime as dt
import hashlib
import json
import sys

import polars as pl

from scalp_engine import ingest, storage
from scalp_engine import insider_prices as P
from scalp_engine.data_access import day_bounds, read_frame
from scalp_engine.signing import load_private_key, sign_artifact

UTC = dt.timezone.utc
FIRST, LAST = dt.date(2022, 2, 1), dt.date(2026, 7, 20)
ALL = (dt.datetime(2022, 1, 3, 5, tzinfo=UTC), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))
CIK_DAYS = 400
PART = "i2-ub-v1"
KEY_PATH = r"D:\scalp-env\keys\p3_signing.pem"


def canonical_sha256(df):
    """ADR 0010 section 1.1: ingested_at dropped, columns sorted, rows sorted by all columns, JSON with sorted keys."""
    df = df.drop([c for c in ("ingested_at",) if c in df.columns])
    df = df.select(sorted(df.columns))
    rows = df.sort(df.columns).to_dicts()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def prior_close_frame(b, sessions, factors):
    """(symbol, i, close, dv20, f): per symbol and calendar index i, the close of session i, the 20-session median
    dollar volume ending at i (missing sessions count 0) and the split factor effective at session i + 1."""
    sess = pl.DataFrame({"d": sessions, "i": list(range(len(sessions)))}, schema={"d": pl.Date, "i": pl.Int32})
    x = b.select("symbol", "d", "close", "volume").join(sess, on="d", how="inner")
    rng = x.group_by("symbol").agg(pl.col("i").min().alias("lo"), pl.col("i").max().alias("hi"))
    rng = rng.with_columns(lo=pl.max_horizontal(pl.col("lo") - (P.DV_SESSIONS - 1), pl.lit(0)).cast(pl.Int32))
    grid = rng.select("symbol", pl.int_ranges("lo", pl.col("hi") + 1, dtype=pl.Int32).alias("i")).explode("i", empty_as_null=True)
    g = grid.join(x.drop("d"), on=["symbol", "i"], how="left").sort(["symbol", "i"])
    g = g.with_columns(dv=(pl.col("close") * pl.col("volume")).fill_null(0.0))
    g = g.with_columns(dv20=pl.col("dv").rolling_median(P.DV_SESSIONS, min_samples=P.DV_SESSIONS).over("symbol"))
    idx = {d: i for i, d in enumerate(sessions)}
    nxt = [(sym, idx[ex] - 1, f) for sym, fs in factors.items() for ex, f in fs if ex in idx and idx[ex] > 0]
    nf = (pl.DataFrame(nxt, schema={"symbol": pl.Utf8, "i": pl.Int32, "f": pl.Float64}, orient="row")
          .group_by(["symbol", "i"]).agg(pl.col("f").product()))
    return g.join(nf, on=["symbol", "i"], how="left").filter(pl.col("close").is_not_null())


def universe_frame(b, sessions, factors, spreads, obs, sic):
    """universe_b_daily rows (ADR 0010 section 1.2) for sessions in [FIRST, LAST].
    spreads: DataFrame symbol, d, spread (close - 5 min, valid samples); obs: symbol, d, cik (filed-symbol
    observations); sic: cik, sic."""
    g = prior_close_frame(b, sessions, factors)
    g = g.with_columns(price=pl.col("close") * pl.col("f").fill_null(1.0))
    # the I1 pre-filter at the prior close: a necessary condition for (b), so every possible member has a row
    g = g.filter((pl.col("dv20") >= P.MIN_DV_B) & ((pl.col("close") >= P.MIN_PRICE_B) | (pl.col("price") >= P.MIN_PRICE_B)))
    sess = pl.DataFrame({"prior_session": sessions[:-1], "session": sessions[1:], "i": list(range(len(sessions) - 1))},
                        schema={"prior_session": pl.Date, "session": pl.Date, "i": pl.Int32})
    g = g.join(sess, on="i", how="inner").filter(pl.col("session").is_between(FIRST, LAST))
    g = g.join(spreads.rename({"d": "prior_session"}), on=["symbol", "prior_session"], how="left")
    g = g.sort(["symbol", "session"])
    o = obs.sort(["symbol", "d"]).rename({"d": "obs_d"})
    g = g.join_asof(o, left_on="session", right_on="obs_d", by="symbol", strategy="backward")
    g = g.with_columns(cik=pl.when((pl.col("session") - pl.col("obs_d")).dt.total_days() <= CIK_DAYS).then(pl.col("cik")))
    s2 = sic.with_columns(sic2=pl.when(pl.col("sic").is_not_null() & (pl.col("sic").str.zfill(4) != "0000"))
                          .then(pl.col("sic").str.zfill(4).str.slice(0, 2))).select("cik", "sic2")
    g = g.join(s2, on="cik", how="left")
    g = g.with_columns(price_ok=pl.col("price") >= P.MIN_PRICE_B, dv_ok=pl.col("dv20") >= P.MIN_DV_B,
                       spread_ok=pl.col("spread").is_not_null() & (pl.col("spread") <= P.MAX_SPREAD_B))
    g = g.with_columns(universe_b=pl.col("price_ok") & pl.col("dv_ok") & pl.col("spread_ok"))
    rk = (g.filter(pl.col("universe_b")).sort(["session", "dv20", "symbol"])
          .with_columns(r=pl.int_range(pl.len()).over("session"), n=pl.len().over("session"))
          .with_columns(dv_decile=((10 * pl.col("r")) // pl.col("n") + 1).cast(pl.Int64)).select("session", "symbol", "dv_decile"))
    g = g.join(rk, on=["session", "symbol"], how="left")
    return g.select("session", "prior_session", "symbol", "cik", "sic2", "price", "dv20", "spread", "price_ok", "dv_ok",
                    "spread_ok", "universe_b", "dv_decile")


def build_universe():
    from scalp_engine import insider_data as D
    sessions = P.session_list()
    b = P.bars()
    factors = P.price_factors(P.actions())
    q = read_frame(storage.data_root() / "raw_quote_samples", *ALL,
                   columns=["symbol", "status", "bid_price", "ask_price", "sample_rule", "ingested_at"])
    q = q.filter((pl.col("sample_rule") == P.CLOSE_RULE) & (pl.col("status") == "ok") & (pl.col("bid_price") > 0)
                 & (pl.col("ask_price") >= pl.col("bid_price")))
    q = q.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
    q = q.sort("ingested_at").unique(subset=["symbol", "d"], keep="last")
    spreads = q.select("symbol", "d", spread=(pl.col("ask_price") - pl.col("bid_price"))
                       / ((pl.col("ask_price") + pl.col("bid_price")) / 2))
    fil = D.filings_frame().select("issuer_cik", "issuer_symbol", "filing_date").drop_nulls().unique()
    obs = fil.select(symbol=pl.col("issuer_symbol").map_elements(P.norm, return_dtype=pl.Utf8), d=pl.col("filing_date"),
                     cik=pl.col("issuer_cik")).drop_nulls().sort(["symbol", "d", "cik"]).unique(subset=["symbol", "d"], keep="last")
    sic = D.sic_frame().select("cik", "sic")
    u = universe_frame(b, sessions, factors, spreads, obs, sic)
    got = ingest.now()
    u = u.with_columns(event_at=pl.col("session").map_elements(lambda d: day_bounds(d)[0], return_dtype=pl.Datetime("us", "UTC")),
                       feed=pl.lit("none"), request_id=pl.lit("derived:" + PART), ingested_at=pl.lit(got).dt.cast_time_unit("us"),
                       source=pl.lit("scalp_engine"), schema_version=pl.lit(1, dtype=pl.Int32))
    u = storage.validate("universe_b_daily", u.select(list(storage.schema("universe_b_daily"))))
    done = storage.completed("universe_b_daily", part=PART)
    for (day,), g in u.partition_by("session", as_dict=True, maintain_order=True).items():
        if storage.partition_key("universe_b_daily", source="scalp_engine", date=day) not in done:
            storage.write_partition("universe_b_daily", g, part=PART, source="scalp_engine", date=day)
    ingest.log(f"universe_b_daily: {u.height} rows, {u['session'].n_unique()} sessions, "
               f"{u.filter(pl.col('universe_b')).height} universe-(b) rows")


def sign_event_set(key_path=KEY_PATH):
    from scalp_engine import insider_rule as R
    ev = read_frame(storage.data_root() / "insider_events", *ALL)
    payload = {"kind": "insider_event_set", "table": "insider_events", "rule_version": R.RULE_VERSION,
               "rows": ev.height, "sha256": canonical_sha256(ev)}
    env = sign_artifact(payload, load_private_key(key_path))
    out = storage.data_root() / "derived" / "i2_event_set.signed.json"
    out.write_text(json.dumps(env, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=1))


def main(argv=None):
    step = (argv or sys.argv[1:] or [""])[0]
    if step == "universe":
        build_universe()
    elif step == "sign":
        sign_event_set()
    else:
        raise SystemExit("usage: python -m scalp_engine.insider_inputs universe|sign")
    return 0


if __name__ == "__main__":
    sys.exit(main())
