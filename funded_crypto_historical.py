"""Research-only historical Kraken spot 4h/1d/1w original-rule replay.

Every scored as-of clock uses completed bars only, including BTC-relative
context; no present-day tickers, balances or future bars influence a past
decision. Liquidity is a rolling spot OHLCV *proxy*, not an authenticated
historical 24h Kraken ticker or Kraken Funded quote. Thus even an original
engine BUY is PROXY_BUY_UNVERIFIED, never broker-ready.
"""
from __future__ import annotations

from collections import Counter
import math
import pandas as pd

from funded_crypto_checks import completed_funded_crypto_candles
from crypto_rule_engine import ScreenerConfig, score_setup

DELTAS = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1),
          "1w": pd.Timedelta(days=7)}
GRACE = pd.Timedelta(minutes=1)
REQUIRED = {"timestamp", "open", "high", "low", "close", "volume"}


def normalize_bars(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in DELTAS:
        raise ValueError("unexpected timeframe")
    if frame is None or frame.empty or not REQUIRED.issubset(frame):
        raise ValueError(f"missing {timeframe} OHLCV")
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="raise", utc=True)
    if data["timestamp"].duplicated().any():
        raise ValueError(f"duplicate {timeframe} timestamps")
    data = data.sort_values("timestamp").reset_index(drop=True)
    for key in ("open", "high", "low", "close", "volume"):
        data[key] = pd.to_numeric(data[key], errors="raise")
        if not data[key].map(math.isfinite).all():
            raise ValueError(f"nonfinite {timeframe} {key}")
    if ((data[["open", "high", "low", "close"]] <= 0).any().any()
            or (data["volume"] < 0).any()
            or (data["high"] < data[["open", "close", "low"]].max(axis=1)).any()
            or (data["low"] > data[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError(f"invalid {timeframe} OHLCV")
    return data


def _asof(frame, timeframe, clock, *, min_bars):
    """Trim by the real close + 1 minute; enforce continuous recent history."""
    view = frame.loc[
        frame["timestamp"] + DELTAS[timeframe] + GRACE <= clock
    ].tail({"4h":180,"1d":365,"1w":220}[timeframe]).reset_index(drop=True)
    if len(view) < min_bars:
        raise ValueError(f"{timeframe} completed history {len(view)}/{min_bars}")
    if clock - (view["timestamp"].iloc[-1] + DELTAS[timeframe]) > DELTAS[timeframe] + GRACE:
        raise ValueError(f"stale as-of {timeframe} history")
    if (view["timestamp"].tail(min_bars).diff().iloc[1:] != DELTAS[timeframe]).any():
        raise ValueError(f"gapped recent {timeframe} history")
    return view


def score_asof(coin, btc, *, coin_name, pair, bar_open, universe_size=33,
               min_daily_bars=35, scorer=score_setup, flattener=None):
    """Run *original* scoring and original BUY routing on exact historical data."""
    if coin_name not in {"BTC", "ETH", "LINK", "BCH", "DOGE", "SHIB", "ARB",
                         "BONK", "AAVE", "AVAX", "BNB", "CRV", "DOT", "FIL",
                         "GRASS", "ADA", "HBAR", "INJ", "KAITO", "LDO", "LTC",
                         "MOODENG", "NEAR", "PNUT", "SOL", "S", "TRUMP", "UNI",
                         "WLD", "XRP", "ALGO", "APT", "ATOM"}:
        raise ValueError("coin absent from frozen funded universe")
    t = pd.Timestamp(bar_open)
    if t.tzinfo is None:
        raise ValueError("naive OHLCV clock")
    t = t.tz_convert("UTC")
    clock = t + DELTAS["4h"] + GRACE
    c4 = _asof(coin["4h"], "4h", clock, min_bars=70)
    cd = _asof(coin["1d"], "1d", clock, min_bars=min_daily_bars)
    cw = _asof(coin["1w"], "1w", clock, min_bars=8)
    b4 = _asof(btc["4h"], "4h", clock, min_bars=70)
    bd = _asof(btc["1d"], "1d", clock, min_bars=min_daily_bars)
    if c4["timestamp"].iloc[-1] != b4["timestamp"].iloc[-1]:
        raise ValueError("coin/BTC 4h clocks not aligned")
    if c4["timestamp"].iloc[-1] != t:
        raise ValueError("decision 4h bar is not latest complete coin candle")

    # Historic quote volume is reconstructed from 6 completed 4h spot base
    # volumes, not current Ticker quoteVolume. It is an explicit PROXY.
    vol_proxy = float((c4["volume"].tail(6) * c4["close"].tail(6)).sum())
    cfg = ScreenerConfig(exchange_id="kraken",quote=pair.split("/")[-1],
                         universe_size=universe_size,min_quote_volume=0,
                         score_threshold=80)
    result = scorer(c4, cd, b4, cfg, dfw=cw, btcd=bd)
    item = {
        "Base":coin_name, "Symbol":pair, "Exchange":"Kraken",
        "Exchange id":"kraken", "24h quote volume":vol_proxy,
        "Execution liquidity pass":vol_proxy>=1_000_000,
        "Execution reason":"research OHLCV proxy only",
    }
    if flattener is None:
        from crypto_market_pipeline import flatten_deep_score
        flattener = flatten_deep_score
    flat = flattener(item, result)
    status = str(flat.get("Swing status") or "UNKNOWN")
    return {
        "Coin":coin_name, "Pair":pair, "Signal open UTC":t.isoformat(),
        "Decision UTC":clock.isoformat(),
        "Original technical state":status,
        "Research class":("PROXY_BUY_UNVERIFIED" if status == "BUY"
                          else "WATCH" if status == "WATCH"
                          else "REJECTED_PASS" if status == "PASS"
                          else "UNKNOWN"),
        "Original technical score":flat.get("Swing score"),
        "Original reason":str(flat.get("Swing reason") or "")[:450],
        "Original entry timing":flat.get("Entry timing"),
        "Original eligible":bool(flat.get("Swing eligible")),
        "Original timing actionable":bool(flat.get("Entry timing actionable")),
        "Original R:R":flat.get("Current R:R"),
        "Entry proxy":flat.get("Active entry"),
        "Stop proxy":flat.get("Invalidation"),
        "Target proxy":flat.get("First technical target"),
        "RS vs BTC %":flat.get("RS vs BTC %"),
        "24h OHLCV quote volume USD proxy":round(vol_proxy,2),
        "Proxy liquidity >= $1m":bool(vol_proxy>=1_000_000),
        "Last 4h open":str(c4["timestamp"].iloc[-1]),
        "Last daily open":str(cd["timestamp"].iloc[-1]),
        "Last weekly open":str(cw["timestamp"].iloc[-1]),
        "Last BTC 4h open":str(b4["timestamp"].iloc[-1]),
        "Real historical ticker volume verified":False,
        "Historical Kraken Funded broker eligible":False,
        "Kraken Funded orders authorised":False,
    }


def replay_coin(coin, btc, *, coin_name, pair, max_anchors=120, horizons=(5,10,20),
                thresholds=(10,20,30), scorer=score_setup,
                flattener=None, anchor_stride=1, min_daily_bars=35):
    if not 1 <= max_anchors <= 2000 or not 1 <= anchor_stride <= 144:
        raise ValueError("max_anchors/anchor_stride outside research guard")
    if not 35 <= min_daily_bars <= 365:
        raise ValueError("invalid daily warmup for historical rulebook")
    history = coin["4h"].reset_index(drop=True)
    end = len(history) - max(horizons)  # every anchor must have a complete horizon
    begin = max(0, end-max_anchors*anchor_stride)
    audits, outcome_rows, errors = [], [], []
    for i in range(begin,end,anchor_stride):
        t = history["timestamp"].iloc[i]
        try:
            row = score_asof(coin,btc,coin_name=coin_name,pair=pair,bar_open=t,
                             scorer=scorer,flattener=flattener,
                             min_daily_bars=min_daily_bars)
        except (ValueError, KeyError, IndexError, ArithmeticError) as exc:
            errors.append({"Coin":coin_name,"Decision bar open":str(t),
                           "Reason":f"{type(exc).__name__}: {str(exc)[:150]}"})
            continue
        audits.append(row)
        start = float(history["close"].iloc[i])
        forward = history["close"].iloc[i+1:i+max(horizons)+1].to_numpy(dtype=float)
        for h in horizons:
            interval = forward[:h]
            if len(interval)<h:
                continue
            up = (interval.max()/start-1)*100
            down = (interval.min()/start-1)*100
            for threshold in thresholds:
                first = next((idx for idx,value in enumerate(interval,1)
                              if (value/start-1)*100 >= threshold),None)
                outcome_rows.append({
                    "Coin":coin_name, "Pair":pair,
                    "Decision bar open":str(t),
                    "Decision UTC":row["Decision UTC"],
                    "Original research class":row["Research class"],
                    "Original reason":row["Original reason"],
                    "Horizon 4h bars":h, "Mover threshold %":threshold,
                    "Later mover":bool(up>=threshold),
                    "First target reach bars":first,
                    "Max later CLOSE %":round(up,4),
                    "Worst later CLOSE %":round(down,4),
                    "Unverified research BUY proxy":row["Research class"]=="PROXY_BUY_UNVERIFIED",
                    "No broker fills established":True,
                })
    return pd.DataFrame(audits),pd.DataFrame(outcome_rows),pd.DataFrame(errors)


def independent_mover_episodes(outcomes):
    """Greedy horizon cooldown; labels descriptive, not independent trades."""
    if outcomes.empty:
        return pd.DataFrame()
    result=[]
    for (coin,horizon,threshold),grp in outcomes.groupby(
            ["Coin","Horizon 4h bars","Mover threshold %"]):
        last_selected = -int(horizon)-1
        for pos,(ix,row) in enumerate(grp.sort_values("Decision bar open").iterrows()):
            if not row["Later mover"] or pos-last_selected <= horizon:
                continue
            last_selected=pos
            result.append({
                "Coin":coin,"Window 4h bars":int(horizon),
                "Threshold %":float(threshold),
                "Episode anchor":row["Decision bar open"],
                "Original class":row["Original research class"],
                "Original reason":row["Original reason"],
                "Max subsequent CLOSE %":row["Max later CLOSE %"],
                "Not an independently executed trade":True,
            })
    return pd.DataFrame(result)


def research_exit_proxy(c4, audits, *, cost_pct=0.8, max_hold_bars=20):
    """Illustrative candidate outcomes, next-4h open, stop-first intrabar.

    Separate from technical qualification; no compounding/account/rule
    validation. A fully corroborated real broker execution cannot be inferred.
    """
    if not 0.5 <= cost_pct <= 5 or not 2 <= max_hold_bars <= 90:
        raise ValueError("invalid conservative friction/holding assumptions")
    if audits.empty:
        return pd.DataFrame()
    history=c4.set_index("timestamp",drop=False)
    records=[]
    for _,item in audits.iterrows():
        if item["Research class"]!="PROXY_BUY_UNVERIFIED":
            continue
        t=pd.Timestamp(item["Signal open UTC"])
        future=history.loc[history.index>t].head(max_hold_bars)
        if len(future)<max_hold_bars:
            continue
        try:
            entry=float(future["open"].iloc[0])*(1+cost_pct/200)
            stop=float(item["Stop proxy"])
            target=float(item["Target proxy"])
        except (TypeError,ValueError,OverflowError):
            continue
        if not math.isfinite(entry+stop+target) or stop>=entry or stop<=0 or target<=entry:
            continue
        close_price=None;why=None
        for _,bar in future.iterrows():
            if bar["open"]<=stop:
                close_price=float(bar["open"])*(1-cost_pct/200);why="STOP GAP"
            elif bar["low"]<=stop:
                close_price=stop*(1-cost_pct/200)
                why="STOP FIRST (TARGET MAY ALSO HIT)" if bar["high"]>=target else "STOP"
            elif bar["high"]>=target:
                close_price=target*(1-cost_pct/200);why="TARGET"
            if why:
                break
        if close_price is None:
            close_price=float(future["close"].iloc[-1])*(1-cost_pct/200)
            why="TIME EXIT"
        risk=entry-stop
        records.append({
            "Coin":item["Coin"],"Signal bar open":str(t),
            "Next 4h ask proxy":round(entry,8),
            "Exit bid proxy":round(close_price,8),
            "Outcome reason":why,
            "Net R proxy":round((close_price-entry)/risk,4),
            "Modeled roundtrip friction %":cost_pct,
            "Kraken Funded fill verified":False,
        })
    return pd.DataFrame(records)
