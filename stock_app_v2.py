from pathlib import Path
from stock_backtest import run_trade_backtest, run_basket_backtest
from strategy_scores_v3 import long_term_analysis, long_term_entry_score, strategy_label
from two_strategy import trade_decision, investment_decision

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
'''            owned = st.checkbox("I already own this ticker", key=f"owned_{res['symbol']}")
            average_buy = None
            if owned:
                average_buy = st.number_input(
                    "My average buy price",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    key=f"avg_buy_{res['symbol']}",
                )

            td = trade_decision(res, owned=owned, average_buy_price=average_buy)
            iv = investment_decision(res, owned=owned, average_buy_price=average_buy)

            left, right = st.columns(2)
            with left:
                st.subheader("TRADE")
                st.metric("Action", td["action"])
                t1, t2, t3 = st.columns(3)
                t1.metric("Technical setup", f"{res['trade_score']:.0f}/100")
                t2.metric("12-month fundamentals", f"{res['one_year_hold_score']:.0f}/100")
                t3.metric("Potential ROI", f"{res['upside_pct']:.1f}%")
                st.write(f"**Entry:** {fmt_price(res['preferred_low'])}–{fmt_price(res['preferred_high'])}")
                st.write(f"**Stronger entry:** {fmt_price(res['strong_low'])}–{fmt_price(res['strong_high'])}")
                st.write(f"**Exit target:** {fmt_price(res['swing_target'])}")
                st.write(f"**Reassess / exit below:** {fmt_price(res['invalidation'])}")
                if owned and td["actual_roi_pct"] is not None:
                    st.write(f"**Your current ROI:** {td['actual_roi_pct']:+.1f}%")
                if td["action"] == "WAIT" and td["reasons"]:
                    st.caption("Waiting because: " + "; ".join(td["reasons"]))

            with right:
                st.subheader("INVESTMENT")
                st.metric("Action", iv["action"])
                i1, i2, i3 = st.columns(3)
                i1.metric("Long-term quality", f"{res['long_term_score']:.1f}/100")
                i2.metric("Entry quality", f"{res['long_term_entry_score']:.0f}/100")
                i3.metric("Valuation", f"{res['valuation_score']:.0f}/20")
                st.write("**Intended hold:** 20–30 years")
                st.write("**Entry:** buy only when long-term quality and entry both qualify")
                st.write("**Exit:** no fixed price target — sell only if the long-term thesis materially breaks")
                if owned and iv["actual_roi_pct"] is not None:
                    st.write(f"**Your current ROI:** {iv['actual_roi_pct']:+.1f}%")
                if iv["action"] == "WAIT" and iv["reasons"]:
                    st.caption("Waiting because: " + "; ".join(iv["reasons"]))

            st.caption(f"Valuation: **{res['valuation_score']:.0f}/20 {res['valuation_label']}** · Business quality: **{res['quality_score']:.0f}/80**")'''
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
                    "FCF/share CAGR": "—" if res["fcf_per_share_cagr_pct"] is None else f"{res['fcf_per_share_cagr_pct']:.1f}%",
                    "ROIC": "—" if res["roic_pct"] is None else f"{res['roic_pct']:.1f}%",
                    "ROE": "—" if res["roe_pct"] is None else f"{res['roe_pct']:.1f}%",
                    "Operating margin": "—" if res["operating_margin_pct"] is None else f"{res['operating_margin_pct']:.1f}%",
                    "Dilution CAGR": "—" if res["dilution_cagr_pct"] is None else f"{res['dilution_cagr_pct']:.2f}%",
                    "Net debt / FCF": "—" if res["net_debt_to_fcf"] is None else f"{res['net_debt_to_fcf']:.2f}x",
                    "Evidence": f"{res['evidence_score']}/10",
                    "Elite gate": "PASS" if res["elite_gate_pass"] else "NO",
                    "Score cap reason": res["score_cap_reason"] or "None",
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
                            c3.metric("LT Compounder", f"{q['LT Compounder']:.1f}/100")
                            c4.metric("LT Entry", f"{q['LT Entry']:.0f}/100")
                            st.write(f"**Strategy:** {q['Strategy']} · **Swing upside:** {q['Upside %']:.1f}%")'''
)


src = src.replace(
    'st.caption("Swing-trade entries + fundamental hold quality. No broker connection or brokerage credentials required.")',
    'st.caption("Two strategies only: TRADE for technical setups you can hold up to ~12 months, and INVESTMENT for 20–30 year holdings. No broker connection required.")',
)
src = src.replace(
    'st.write("**Hold Quality:** growth, margins, debt, cash flow and analyst outlook.")',
    'st.write("**TRADE:** technical setup + 10%+ upside + fundamentals good enough for a ~12-month hold.")\n    st.write("**INVESTMENT:** long-term quality + valuation + attractive entry for a 20–30 year hold.")',
)
src = src.replace(
    'st.header("Scoring")',
    'st.header("Two strategies")',
)


src = src.replace(
'''                    "Dividend yield": "—" if res["dividend_yield"] is None else f"{res['dividend_yield']:.2f}%",''',
'''                    "Dividend yield": (
                        "Variable/special dividend policy"
                        if str(res.get("industry") or "").lower().startswith("insurance")
                        else ("—" if res["dividend_yield"] is None else f"{res['dividend_yield']:.2f}%")
                    ),'''
)

src = src.replace(
'''            if res.get("valuation_warning"):
                st.warning("Valuation caution: " + res["valuation_warning"] + ".")''',
'''            if res.get("valuation_warning"):
                st.warning("Valuation caution: " + res["valuation_warning"] + ".")
            if res.get("sector_model") == "Insurance preliminary":
                st.info("Insurer note: trailing dividend yield can be distorted by variable/special dividends. Generic FCF, FCF/share and ROIC are shown for reference only; the insurer score is based on insurer-appropriate metrics and remains capped pending combined-ratio, reserve and capital review.")'''
)

src = src.replace(
'''**Hold Quality /100** rewards market size, revenue and EPS growth, profit margin, manageable debt, positive free cash flow, analyst upside/coverage and sensible dividend/payout characteristics. Growth stocks are not automatically penalised for paying no dividend.''',
'''**1-Year Hold /100** uses Business Quality /80 plus Valuation /20 and asks whether we are comfortable owning the company for roughly 6–12 months if a swing takes longer.

**Long-Term Compounder /100** is deliberately strict. It scores multi-year revenue/earnings consistency, FCF and FCF-per-share compounding, ROIC/profitability, balance-sheet resilience and dilution discipline. Scores of **90+** must also pass an elite gate for evidence quality, growth, per-share cash compounding, ROIC, dilution and debt.

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
    '''st.subheader("Two-strategy rankings")
                    trade_ranked = ranked.copy()
                    trade_ranked["Trade Action"] = trade_ranked.apply(
                        lambda r: "BUY" if (
                            r["Trade"] >= 85 and
                            r["R:R"] >= 2 and
                            r["Upside %"] >= 10 and
                            (bool(r["Preferred now"]) or bool(r["Strong now"])) and
                            r["1Y Hold"] >= 70 and
                            r["Valuation"] >= 10
                        ) else "WAIT",
                        axis=1,
                    )
                    trade_ranked = trade_ranked.sort_values(
                        ["Trade Action", "Trade", "Upside %"],
                        ascending=[True, False, False],
                    )

                    invest_ranked = ranked.copy()
                    invest_ranked["Investment Action"] = invest_ranked.apply(
                        lambda r: "BUY" if (
                            r["LT Compounder"] >= 90 and
                            r["LT Entry"] >= 70
                        ) else "WAIT",
                        axis=1,
                    )
                    invest_ranked = invest_ranked.sort_values(
                        ["Investment Action", "LT Compounder", "LT Entry"],
                        ascending=[True, False, False],
                    )

                    lane1, lane2 = st.tabs(["TRADE", "INVESTMENT"])
                    with lane1:
                        st.caption("Technical setup + 10% or more modelled upside + fundamentals strong enough to hold for roughly 12 months if needed.")
                        st.dataframe(
                            trade_ranked[[
                                "Ticker", "Trade Action", "Trade", "1Y Hold", "Valuation",
                                "Price", "Preferred entry", "Strong entry", "Target",
                                "Upside %", "R:R"
                            ]],
                            hide_index=True,
                            use_container_width=True,
                        )

                    with lane2:
                        st.caption("20–30 year candidates: long-term business quality first, then valuation and entry quality.")
                        st.dataframe(
                            invest_ranked[[
                                "Ticker", "Investment Action", "LT Compounder", "LT Entry",
                                "Valuation", "Valuation rating", "Price", "1Y Hold"
                            ]],
                            hide_index=True,
                            use_container_width=True,
                        )

                    st.subheader("Combined shortlist")'''
)


src = src.replace(
'''                st.dataframe(pd.DataFrame({"Metric": rows.keys(), "Value": rows.values()}), hide_index=True, use_container_width=True)
        else:''',
'''                st.dataframe(pd.DataFrame({"Metric": rows.keys(), "Value": rows.values()}), hide_index=True, use_container_width=True)

            with st.expander("Strict long-term evidence", expanded=True):
                e1, e2, e3, e4 = st.columns(4)
                e1.metric("FCF/share CAGR", "—" if res["fcf_per_share_cagr_pct"] is None else f"{res['fcf_per_share_cagr_pct']:.1f}%")
                e2.metric("ROIC", "—" if res["roic_pct"] is None else f"{res['roic_pct']:.1f}%")
                e3.metric("Dilution CAGR", "—" if res["dilution_cagr_pct"] is None else f"{res['dilution_cagr_pct']:.2f}%")
                e4.metric("Net debt / FCF", "—" if res["net_debt_to_fcf"] is None else f"{res['net_debt_to_fcf']:.2f}x")

                g1, g2, g3 = st.columns(3)
                g1.metric("Evidence", f"{res['evidence_score']}/10")
                g2.metric("Elite gate", "PASS" if res["elite_gate_pass"] else "NO")
                g3.metric("LT score", f"{res['long_term_score']:.1f}/100")

                st.caption(f"Sector model: **{res.get('sector_model', 'Generic')}** · Industry: **{res.get('industry') or '—'}**")
                if res.get("book_value_per_share_cagr_pct") is not None:
                    st.metric("Book value/share CAGR", f"{res['book_value_per_share_cagr_pct']:.1f}%")

                if res["score_cap_reason"]:
                    st.warning("Score cap: " + res["score_cap_reason"])
                else:
                    st.success("No long-term score cap is currently being applied.")
        else:'''
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
st.subheader("Dedicated Investment Scan")
st.caption("Quality-first scan for 20–30 year investment candidates. Stocks do not need a strong short-term technical setup to qualify.")

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
                    "FCF/share CAGR %": lt["fcf_per_share_cagr_pct"],
                    "ROIC %": lt["roic_pct"],
                    "ROE %": lt["roe_pct"],
                    "Operating margin %": lt["operating_margin_pct"],
                    "Dilution CAGR %": lt["dilution_cagr_pct"],
                    "Net debt / FCF": lt["net_debt_to_fcf"],
                    "Evidence /10": lt["evidence_score"],
                    "Elite gate": lt["elite_gate_pass"],
                    "Sector model": lt.get("sector_model", "Generic"),
                    "Book value/share CAGR %": lt.get("book_value_per_share_cagr_pct"),
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

            elite_candidates = lt_df[
                (lt_df["LT Compounder"] >= 90) &
                (lt_df["LT Entry"] >= 70)
            ]
            research_candidates = lt_df[
                (lt_df["LT Compounder"] >= 82) &
                (lt_df["LT Compounder"] < 90) &
                (lt_df["LT Entry"] >= 70)
            ]
            elite_wait = lt_df[
                (lt_df["LT Compounder"] >= 90) &
                (lt_df["LT Entry"] < 70)
            ]

            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Elite candidates", len(elite_candidates))
            q2.metric("Research candidates", len(research_candidates))
            q3.metric("Elite — wait for entry", len(elite_wait))
            q4.metric("Companies scored", len(lt_df))

            st.dataframe(
                lt_df[[
                    "Ticker", "Company", "LT Compounder", "LT Entry", "Strategy",
                    "Valuation", "Valuation rating", "1Y Hold", "Swing", "Signal",
                    "Revenue CAGR %", "Earnings CAGR %", "FCF/share CAGR %",
                    "ROIC %", "ROE %", "Operating margin %", "Dilution CAGR %",
                    "Net debt / FCF", "Evidence /10", "Elite gate", "Sector model",
                    "Book value/share CAGR %", "Market cap bn", "Price"
                ]],
                hide_index=True,
                use_container_width=True,
            )

            st.caption("Long-term labels are research signals, not validated forecasts. A 25–35 year thesis should ultimately be confirmed with business durability, competitive position, capital allocation and sector-specific analysis before any investment decision.")

