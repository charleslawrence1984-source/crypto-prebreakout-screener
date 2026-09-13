from pathlib import Path
from stock_backtest import run_trade_backtest, run_basket_backtest
from strategy_scores import long_term_analysis, long_term_entry_score, strategy_label

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
'''    fund = valuation_fundamental_analysis(symbol, tech["price"])
    sc = classify(tech["trade_score"], fund["hold_score"], fund["valuation_score"])
    return {
        "symbol": symbol,
        **tech,
        **fund,
        "opportunity_score": sc.opportunity,
        "classification": sc.classification,
    }''',
'''    fund = valuation_fundamental_analysis(symbol, tech["price"])
    lt = long_term_analysis(symbol, tech["price"], fund)
    lt_entry = long_term_entry_score(symbol, tech["price"], fund["valuation_score"], tech)
    trade_signal = signal_label(tech["trade_score"], tech["rr"], tech["in_preferred_zone"], tech["in_strong_zone"])
    sc = classify(tech["trade_score"], fund["hold_score"], fund["valuation_score"])
    strategy = strategy_label(
        trade_signal,
        fund["hold_score"],
        fund["valuation_score"],
        lt["long_term_score"],
        lt_entry["long_term_entry_score"],
    )
    return {
        "symbol": symbol,
        **tech,
        **fund,
        **lt,
        **lt_entry,
        "one_year_hold_score": fund["hold_score"],
        "trade_signal": trade_signal,
        "strategy": strategy,
        "opportunity_score": sc.opportunity,
        "classification": sc.classification,
    }'''
)

src = src.replace(
'''            fund = valuation_fundamental_analysis(sym, float(row["Price"]))
            sc = classify(float(row["Trade"]), fund["hold_score"], fund["valuation_score"])
            out.append({''',
'''            fund = valuation_fundamental_analysis(sym, float(row["Price"]))
            tech_full = technical_analysis(sym)
            lt = long_term_analysis(sym, float(row["Price"]), fund)
            lt_entry = long_term_entry_score(sym, float(row["Price"]), fund["valuation_score"], tech_full)
            trade_signal = signal_label(float(row["Trade"]), float(row["R:R"]), bool(row["Preferred now"]), bool(row["Strong now"]))
            sc = classify(float(row["Trade"]), fund["hold_score"], fund["valuation_score"])
            strategy = strategy_label(
                trade_signal,
                fund["hold_score"],
                fund["valuation_score"],
                lt["long_term_score"],
                lt_entry["long_term_entry_score"],
            )
            out.append({'''
)

src = src.replace(
'''                "Ticker": sym,
                "Trade": float(row["Trade"]),
                "Hold": fund["hold_score"],''',
'''                "Ticker": sym,
                "Trade": float(row["Trade"]),
                "Hold": fund["hold_score"],
                "1Y Hold": fund["hold_score"],
                "LT Compounder": lt["long_term_score"],
                "LT Entry": lt_entry["long_term_entry_score"],
                "Strategy": strategy,
                "Valuation": fund["valuation_score"],
                "Valuation rating": fund["valuation_label"],''',
)

src = src.replace(
'''            a, b, c, d = st.columns(4)
            a.metric("Trade Setup", f"{res['trade_score']:.0f}/100")
            b.metric("Hold Quality", f"{res['hold_score']:.0f}/100")
            c.metric("Opportunity", f"{res['opportunity_score']:.0f}/100")
            d.metric("Classification", res["classification"])''',
'''            a, b, c, d = st.columns(4)
            a.metric("Swing Score", f"{res['trade_score']:.0f}/100", res["trade_signal"])
            b.metric("1-Year Hold", f"{res['one_year_hold_score']:.0f}/100")
            c.metric("LT Compounder", f"{res['long_term_score']:.0f}/100", res["long_term_label"])
            d.metric("LT Entry", f"{res['long_term_entry_score']:.0f}/100", res["long_term_entry_label"])
            st.info(f"Strategy: **{res['strategy']}**")
            st.caption(f"Overall opportunity: **{res['opportunity_score']:.0f}/100** · Valuation: **{res['valuation_score']:.0f}/20 {res['valuation_label']}** · Business quality: **{res['quality_score']:.0f}/80**")'''
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
                    "Long-term score": f"{res['long_term_score']:.1f}/100 — {res['long_term_label']}",
                    "Long-term entry": f"{res['long_term_entry_score']:.1f}/100 — {res['long_term_entry_label']}",
                    "Revenue CAGR": "—" if res["revenue_cagr_pct"] is None else f"{res['revenue_cagr_pct']:.1f}%",
                    "Earnings CAGR": "—" if res["earnings_cagr_pct"] is None else f"{res['earnings_cagr_pct']:.1f}%",
                    "ROE": "—" if res["roe_pct"] is None else f"{res['roe_pct']:.1f}%",
                    "Operating margin": "—" if res["operating_margin_pct"] is None else f"{res['operating_margin_pct']:.1f}%",
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
'''                "1Y Hold": r["one_year_hold_score"],
                "LT Compounder": r["long_term_score"],
                "LT Entry": r["long_term_entry_score"],
                "Strategy": r["strategy"],
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
                            c1.metric("Swing", f"{q['Trade']:.0f}/100")
                            c2.metric("1Y Hold", f"{q['1Y Hold']:.0f}/100")
                            c3.metric("LT Compounder", f"{q['LT Compounder']:.0f}/100")
                            c4.metric("LT Entry", f"{q['LT Entry']:.0f}/100")
                            st.write(f"**Strategy:** {q['Strategy']} · **Swing upside:** {q['Upside %']:.1f}%")'''
)


src = src.replace(
    'st.caption("Swing-trade entries + fundamental hold quality. No broker connection or brokerage credentials required.")',
    'st.caption("Three decision lanes: swing trades, 1-year holds, and 25–35 year compounders. No broker connection required.")',
)
src = src.replace(
    'st.write("**Hold Quality:** growth, margins, debt, cash flow and analyst outlook.")',
    'st.write("**1-Year Hold:** growth, valuation, margins, debt, cash flow and analyst outlook.")\n    st.write("**Long-Term Compounder:** multi-year growth consistency, profitability, cash generation, balance sheet, dilution and durability.")',
)
src = src.replace(
    'st.header("Scoring")',
    'st.header("Three decision lanes")',
)

src = src.replace(
'''**Hold Quality /100** rewards market size, revenue and EPS growth, profit margin, manageable debt, positive free cash flow, analyst upside/coverage and sensible dividend/payout characteristics. Growth stocks are not automatically penalised for paying no dividend.''',
'''**1-Year Hold /100** uses Business Quality /80 plus Valuation /20 and asks whether we are comfortable owning the company for roughly 6–12 months if a swing takes longer.

**Long-Term Compounder /100** is separate. It scores multi-year revenue/earnings consistency, profitability and capital efficiency, free-cash-flow durability, balance-sheet strength, dilution discipline and business scale/durability.

**Long-Term Entry /100** combines valuation with the current discount from the 52-week high, position versus the 200-day average and whether price is in an attractive technical entry zone.'''
)


src = src.replace(
'''def chart(result: Dict) -> go.Figure:''',
'''def signal_label(trade, rr, preferred, strong):
    if not (preferred or strong) or rr < 2:
        return "WAIT"
    if trade >= 90:
        return "ELITE"
    if trade >= 85:
        return "ACTIONABLE"
    if trade >= 80:
        return "WATCH"
    return "WAIT"


def chart(result: Dict) -> go.Figure:'''
)

src = src.replace(
'''                "Preferred now": bool(row["Preferred now"]),
                "Strong now": bool(row["Strong now"]),''',
'''                "Preferred now": bool(row["Preferred now"]),
                "Strong now": bool(row["Strong now"]),
                "Signal": signal_label(float(row["Trade"]), float(row["R:R"]), bool(row["Preferred now"]), bool(row["Strong now"])),'''
)

src = src.replace(
'''                            "Ticker", "Opportunity", "Trade", "Hold", "Valuation", "Valuation rating", "Type", "Price",''',
'''                            "Ticker", "Signal", "Strategy", "Opportunity", "Trade", "1Y Hold", "LT Compounder", "LT Entry", "Valuation", "Valuation rating", "Type", "Price",'''
)


src = src.replace(
    'st.subheader("Best overall opportunities")',
    '''st.subheader("Three-lane rankings")
                    swing_ranked = ranked.sort_values(["Trade", "Upside %"], ascending=[False, False])
                    one_year_ranked = ranked.sort_values(["1Y Hold", "Valuation"], ascending=[False, False])
                    long_term_ranked = ranked.sort_values(["LT Compounder", "LT Entry"], ascending=[False, False])

                    lane1, lane2, lane3 = st.tabs(["Swing", "Swing → 1Y Hold", "Long-Term"])
                    with lane1:
                        st.caption("Prioritises validated swing quality: Trade Score, active entry zone and R:R.")
                        st.dataframe(
                            swing_ranked[[
                                "Ticker", "Signal", "Strategy", "Trade", "Price",
                                "Preferred entry", "Strong entry", "Target", "Upside %",
                                "R:R", "1Y Hold", "LT Compounder"
                            ]],
                            hide_index=True,
                            use_container_width=True,
                        )
                    with lane2:
                        st.caption("Prioritises companies we would be comfortable holding for roughly 6–12 months if the swing takes longer.")
                        st.dataframe(
                            one_year_ranked[[
                                "Ticker", "Strategy", "1Y Hold", "Valuation", "Valuation rating",
                                "Trade", "Signal", "Price", "Target", "Upside %", "R:R"
                            ]],
                            hide_index=True,
                            use_container_width=True,
                        )
                    with lane3:
                        st.caption("Ranks long-term business quality first, then current long-term entry attractiveness. This long-term model is still provisional and has not yet been historically validated.")
                        st.dataframe(
                            long_term_ranked[[
                                "Ticker", "Strategy", "LT Compounder", "LT Entry",
                                "Valuation", "Valuation rating", "1Y Hold", "Trade", "Price"
                            ]],
                            hide_index=True,
                            use_container_width=True,
                        )

                    st.subheader("Combined shortlist")'''
)

src = src.replace(
'''**Opportunity Score** is currently 55% Trade Setup + 45% Hold Quality.''',
'''**Opportunity Score** is currently 55% Trade Setup + 45% Hold Quality.

**Validated trade signal rule**
- **80–84:** WATCH
- **85–89:** ACTIONABLE
- **90+:** ELITE / rare
- A live trade signal also requires **R:R ≥ 2.0** and price inside the **preferred or strong entry zone**.'''
)

exec(compile(src, "stock_app.py", "exec"), globals(), globals())


st.divider()
st.subheader("Dedicated Long-Term Compounder Scan")
st.caption("Quality-first scan for 25–35 year candidates. Unlike the broad swing scan, stocks do not need a strong short-term Trade Score to be considered. The long-term model is provisional until separately validated.")

ltc1, ltc2, ltc3 = st.columns(3)
with ltc1:
    lt_universe_label = st.selectbox(
        "Long-term universe",
        list(PUBLIC_UNIVERSES.keys()),
        index=0,
        key="lt_universe_label",
    )
with ltc2:
    lt_max = st.selectbox(
        "Companies to score",
        [25, 50, 100, 250],
        index=1,
        key="lt_max",
        help="If fewer than the full universe are selected, the app samples evenly across the universe rather than taking only alphabetically early tickers.",
    )
with ltc3:
    lt_min_cap = st.number_input(
        "Minimum market cap (bn)",
        min_value=0.0,
        max_value=500.0,
        value=3.0,
        step=1.0,
        key="lt_min_cap",
    )

if st.button("Run long-term compounder scan", key="run_lt_compounder_scan", type="primary"):
    with st.spinner("Loading long-term universe…"):
        lt_universe = get_universe(PUBLIC_UNIVERSES[lt_universe_label])

    if not lt_universe:
        st.error("The selected public universe could not be loaded.")
    else:
        if lt_max >= len(lt_universe):
            lt_symbols = lt_universe
        else:
            idx = np.linspace(0, len(lt_universe) - 1, lt_max, dtype=int)
            lt_symbols = [lt_universe[i] for i in idx]

        st.info(f"Scoring {len(lt_symbols)} companies from a universe of {len(lt_universe):,}. This can take a few minutes because multi-year fundamentals are checked for each company.")

        lt_rows = []
        prog = st.progress(0)
        for i, sym in enumerate(lt_symbols):
            try:
                tech = technical_analysis(sym)
                if not tech:
                    prog.progress((i + 1) / len(lt_symbols))
                    continue

                fund = valuation_fundamental_analysis(sym, tech["price"])
                mcap = safe(fund.get("market_cap"))
                if not np.isnan(mcap) and mcap < lt_min_cap * 1_000_000_000:
                    prog.progress((i + 1) / len(lt_symbols))
                    continue

                lt = long_term_analysis(sym, tech["price"], fund)
                entry = long_term_entry_score(sym, tech["price"], fund["valuation_score"], tech)
                sig = signal_label(
                    tech["trade_score"],
                    tech["rr"],
                    tech["in_preferred_zone"],
                    tech["in_strong_zone"],
                )
                strat = strategy_label(
                    sig,
                    fund["hold_score"],
                    fund["valuation_score"],
                    lt["long_term_score"],
                    entry["long_term_entry_score"],
                )

                lt_rows.append({
                    "Ticker": sym,
                    "Company": fund.get("name", sym),
                    "LT Compounder": lt["long_term_score"],
                    "LT Entry": entry["long_term_entry_score"],
                    "Strategy": strat,
                    "1Y Hold": fund["hold_score"],
                    "Valuation": fund["valuation_score"],
                    "Valuation rating": fund["valuation_label"],
                    "Swing": tech["trade_score"],
                    "Signal": sig,
                    "Price": tech["price"],
                    "Revenue CAGR %": lt["revenue_cagr_pct"],
                    "Earnings CAGR %": lt["earnings_cagr_pct"],
                    "ROE %": lt["roe_pct"],
                    "Operating margin %": lt["operating_margin_pct"],
                    "Market cap bn": None if np.isnan(mcap) else round(mcap / 1_000_000_000, 1),
                })
            except Exception:
                pass
            prog.progress((i + 1) / len(lt_symbols))
        prog.empty()

        if not lt_rows:
            st.warning("No companies returned enough long-term data under those filters.")
        else:
            lt_df = pd.DataFrame(lt_rows).sort_values(
                ["LT Compounder", "LT Entry"],
                ascending=[False, False],
            ).reset_index(drop=True)

            buy_now = lt_df[
                (lt_df["LT Compounder"] >= 85) &
                (lt_df["LT Entry"] >= 70)
            ]
            elite_wait = lt_df[
                (lt_df["LT Compounder"] >= 85) &
                (lt_df["LT Entry"] < 70)
            ]

            q1, q2, q3 = st.columns(3)
            q1.metric("Long-term candidates", len(buy_now))
            q2.metric("Elite — wait for entry", len(elite_wait))
            q3.metric("Companies scored", len(lt_df))

            st.dataframe(
                lt_df[[
                    "Ticker", "Company", "LT Compounder", "LT Entry", "Strategy",
                    "Valuation", "Valuation rating", "1Y Hold", "Swing", "Signal",
                    "Revenue CAGR %", "Earnings CAGR %", "ROE %",
                    "Operating margin %", "Market cap bn", "Price"
                ]],
                hide_index=True,
                use_container_width=True,
            )

            st.caption("Long-term labels are research signals, not validated forecasts. A 25–35 year thesis should ultimately be confirmed with business durability, competitive position, capital allocation and sector-specific analysis before any investment decision.")

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


st.divider()
st.subheader("Multi-stock Threshold Validation")
st.caption("Aggregates the same 5-year Trade Score backtest across a basket of stocks so one ticker cannot decide the alert threshold. Use the diversified preset for a less selection-biased test.")

basket_preset = st.selectbox(
    "Validation basket preset",
    ["Diversified 25 (recommended)", "Current opportunity 7"],
    index=0,
    key="validation_basket_preset",
)
default_basket = (
    "AAPL, MSFT, GOOGL, AMZN, META, NVDA, AMD, JPM, BAC, XOM, CVX, "
    "CAT, DE, UNH, JNJ, COST, WMT, HD, NEE, PLD, ON, TER, OXY, STRL, FLEX"
    if basket_preset == "Diversified 25 (recommended)"
    else "TER, ON, OXY, STRL, FLEX, ST, XOM"
)
basket_text = st.text_input(
    "Validation basket",
    value=default_basket,
    key="validation_basket",
    help="Comma-separated tickers. The diversified preset reduces selection bias from testing only today's top-ranked stocks.",
)
basket_horizon = st.selectbox(
    "Basket forward window",
    [10, 20, 40],
    index=1,
    format_func=lambda x: f"{x} trading days",
    key="basket_horizon",
)
filter1, filter2 = st.columns(2)
with filter1:
    basket_min_rr = st.selectbox("Minimum R:R", [0.0, 1.5, 2.0, 2.5], index=2, key="basket_min_rr")
with filter2:
    basket_zone = st.selectbox(
        "Entry zone",
        ["either", "preferred", "strong"],
        index=0,
        format_func=lambda x: {"either":"Preferred or strong","preferred":"Preferred only","strong":"Strong only"}[x],
        key="basket_zone",
    )

if st.button("Run multi-stock validation", key="run_basket_validation"):
    basket = [x.strip().upper() for x in basket_text.split(",") if x.strip()]
    with st.spinner(f"Backtesting {len(basket)} stocks across 80+, 85+ and 90+…"):
        basket_events, basket_summary, per_stock = run_basket_backtest(
            basket,
            technical_from_df,
            thresholds=(80, 85, 90),
            horizon=basket_horizon,
            cooldown=10,
            min_rr=basket_min_rr,
            zone_mode=basket_zone,
        )

    if basket_summary.empty:
        st.warning("No qualifying historical signals were found across this basket.")
    else:
        st.dataframe(basket_summary, hide_index=True, use_container_width=True)

        eligible = basket_summary[basket_summary["Signals"] >= 15].copy()
        if eligible.empty:
            eligible = basket_summary.copy()

        best = eligible.sort_values(
            ["Hit +10%", "Invalidation hit", "Signals"],
            ascending=[False, True, False],
        ).iloc[0]

        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Provisional threshold", f"{int(best['Threshold'])}+")
        v2.metric("Signals", int(best["Signals"]))
        v3.metric("10% hit rate", f"{best['Hit +10%']:.1f}%")
        v4.metric("Invalidation hit", f"{best['Invalidation hit']:.1f}%")

        st.caption("For threshold selection, the app prefers at least 15 aggregate signals when possible so a tiny sample does not win just by chance. Use the R:R and entry-zone filters to test whether selectivity improves the edge.")

        with st.expander("Per-stock validation"):
            st.dataframe(per_stock.sort_values(["Threshold", "Ticker"]), hide_index=True, use_container_width=True)
