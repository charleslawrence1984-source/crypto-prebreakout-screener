from pathlib import Path
from stock_backtest import run_trade_backtest

src = Path("stock_app.py").read_text(encoding="utf-8")

src = src.replace(
    "import yfinance as yf\n",
    "import yfinance as yf\nfrom valuation import fundamental_analysis as valuation_fundamental_analysis\n",
)

src = src.replace(
    'fundamental_analysis(symbol, tech["price"])',
    'valuation_fundamental_analysis(symbol, tech["price"])',
)
src = src.replace(
    'fundamental_analysis(sym, float(row["Price"]))',
    'valuation_fundamental_analysis(sym, float(row["Price"]))',
)

src = src.replace(
'''def classify(trade: float, hold: float) -> Scores:
    opp = round(0.55*trade + 0.45*hold, 1)
    if trade >= 75 and hold >= 65:
        c = "Swing-to-hold"
    elif trade >= 75:
        c = "Swing only"
    elif hold >= 80 and trade >= 55:
        c = "Core opportunity"
    elif trade >= 60 and hold >= 60:
        c = "Developing"
    else:
        c = "Watch / wait"
    return Scores(trade, hold, opp, c)''',
'''def classify(trade: float, hold: float, valuation: float = 10) -> Scores:
    opp = round(0.55*trade + 0.45*hold, 1)
    if trade >= 75 and hold >= 65 and valuation >= 12:
        c = "Swing-to-hold"
    elif trade >= 75 and hold >= 65 and valuation >= 8:
        c = "Swing-to-hold (valuation caution)"
    elif trade >= 75:
        c = "Swing only"
    elif hold >= 80 and trade >= 55 and valuation >= 12:
        c = "Core opportunity"
    elif trade >= 60 and hold >= 60:
        c = "Developing"
    else:
        c = "Watch / wait"
    return Scores(trade, hold, opp, c)'''
)

src = src.replace(
    'sc = classify(tech["trade_score"], fund["hold_score"])',
    'sc = classify(tech["trade_score"], fund["hold_score"], fund["valuation_score"])',
)
src = src.replace(
    'sc = classify(float(row["Trade"]), fund["hold_score"])',
    'sc = classify(float(row["Trade"]), fund["hold_score"], fund["valuation_score"])',
)

src = src.replace(
'''            a, b, c, d = st.columns(4)
            a.metric("Trade Setup", f"{res['trade_score']:.0f}/100")
            b.metric("Hold Quality", f"{res['hold_score']:.0f}/100")
            c.metric("Opportunity", f"{res['opportunity_score']:.0f}/100")
            d.metric("Classification", res["classification"])''',
'''            a, b, c, d = st.columns(4)
            a.metric("Trade Setup", f"{res['trade_score']:.0f}/100")
            b.metric("Hold Quality", f"{res['hold_score']:.0f}/100")
            c.metric("Valuation", f"{res['valuation_score']:.0f}/20", res["valuation_label"])
            d.metric("Opportunity", f"{res['opportunity_score']:.0f}/100")
            st.caption(f"Classification: **{res['classification']}** · Business quality: **{res['quality_score']:.0f}/80**")'''
)

src = src.replace(
'''            if res["in_preferred_zone"]:
                st.success("Current price is inside the preferred entry zone.")
            elif res["in_strong_zone"]:
                st.success("Current price is inside the strong entry zone.")''',
'''            if res["in_preferred_zone"]:
                st.success("Current price is inside the preferred entry zone.")
            elif res["in_strong_zone"]:
                st.success("Current price is inside the strong entry zone.")
            if res.get("valuation_warning"):
                st.warning("Valuation caution: " + res["valuation_warning"] + ".")'''
)

src = src.replace(
'''                    "Analysts": res["analyst_count"],
                }''',
'''                    "Analysts": res["analyst_count"],
                    "Business quality": f"{res['quality_score']:.1f}/80",
                    "Valuation": f"{res['valuation_score']:.1f}/20 — {res['valuation_label']}",
                    "Forward P/E": "—" if np.isnan(safe(res["forward_pe"])) else f"{res['forward_pe']:.2f}",
                    "PEG": "—" if np.isnan(safe(res["peg"])) else f"{res['peg']:.2f}",
                    "Price / sales": "—" if np.isnan(safe(res["price_sales"])) else f"{res['price_sales']:.2f}",
                    "FCF yield": "—" if np.isnan(safe(res["fcf_yield"])) else f"{res['fcf_yield']:.2f}%",
                    "EV / EBITDA": "—" if np.isnan(safe(res["ev_ebitda"])) else f"{res['ev_ebitda']:.2f}",
                }'''
)

src = src.replace(
'''                "Hold": fund["hold_score"],
                "Opportunity": sc.opportunity,''',
'''                "Hold": fund["hold_score"],
                "Valuation": fund["valuation_score"],
                "Valuation rating": fund["valuation_label"],
                "Opportunity": sc.opportunity,'''
)

src = src.replace(
'''                "Hold": r["hold_score"],
                "Opportunity": r["opportunity_score"],''',
'''                "Hold": r["hold_score"],
                "Valuation": r["valuation_score"],
                "Opportunity": r["opportunity_score"],'''
)

src = src.replace(
'''                            "Ticker", "Opportunity", "Trade", "Hold", "Type", "Price",''',
'''                            "Ticker", "Opportunity", "Trade", "Hold", "Valuation", "Valuation rating", "Type", "Price",'''
)

src = src.replace(
'''                            c1, c2, c3 = st.columns(3)
                            c1.metric("Trade", f"{q['Trade']:.0f}/100")
                            c2.metric("Hold", f"{q['Hold']:.0f}/100")
                            c3.metric("Upside", f"{q['Upside %']:.1f}%")''',
'''                            c1, c2, c3, c4 = st.columns(4)
                            c1.metric("Trade", f"{q['Trade']:.0f}/100")
                            c2.metric("Hold", f"{q['Hold']:.0f}/100")
                            c3.metric("Valuation", f"{q['Valuation']:.0f}/20")
                            c4.metric("Upside", f"{q['Upside %']:.1f}%")'''
)

src = src.replace(
'''**Hold Quality /100** rewards market size, revenue and EPS growth, profit margin, manageable debt, positive free cash flow, analyst upside/coverage and sensible dividend/payout characteristics. Growth stocks are not automatically penalised for paying no dividend.''',
'''**Hold Quality /100** is split into **Business Quality /80** plus **Valuation /20**. Business Quality rewards size, revenue and EPS growth, margins, manageable debt, positive free cash flow, analyst outlook and shareholder discipline. Valuation uses P/E, PEG, price-to-sales, free-cash-flow yield and EV/EBITDA when available. Missing valuation data is treated neutrally rather than as automatically cheap.'''
)

exec(compile(src, "stock_app.py", "exec"), globals(), globals())


st.divider()
st.subheader("Historical Trade Score Backtest")
st.caption("Replays the current Trade Score on the last 5 years of daily data. Signals only count when price was inside the model's preferred or strong entry zone.")

bt1, bt2 = st.columns([2, 1])
with bt1:
    bt_symbol = st.text_input("Backtest ticker", value="FLNC", key="stock_bt_symbol")
with bt2:
    bt_horizon = st.selectbox("Forward window", [10, 20, 40], index=1, format_func=lambda x: f"{x} trading days", key="stock_bt_horizon")

if st.button("Run historical backtest", key="run_stock_backtest", type="primary"):
    with st.spinner(f"Replaying {bt_symbol.upper()} over 5 years…"):
        events, summary = run_trade_backtest(
            bt_symbol.strip().upper(),
            technical_from_df,
            thresholds=(80, 85, 90),
            horizon=bt_horizon,
            cooldown=10,
        )

    if summary.empty:
        st.warning("No qualifying historical signals were found for that ticker and entry rule.")
    else:
        st.dataframe(summary, hide_index=True, use_container_width=True)
        st.caption("Hit rates require the target to be reached before the model's invalidation level. The 10-day cooldown reduces repeated counting of the same setup.")

        best = summary.sort_values(["Hit +10%", "Invalidation hit"], ascending=[False, True]).iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Best threshold", f"{int(best['Threshold'])}+")
        c2.metric("10% hit rate", f"{best['Hit +10%']:.1f}%")
        c3.metric("Median max return", f"{best['Median max return %']:.1f}%")
        c4.metric("Invalidation hit", f"{best['Invalidation hit']:.1f}%")

        with st.expander("Historical signals"):
            st.dataframe(events.sort_values("Date", ascending=False), hide_index=True, use_container_width=True)

st.caption("Backtest limitations: daily OHLC cannot reveal the exact intraday order when both a target and invalidation trade in the same session. This panel is for model calibration, not a guarantee of future returns.")
