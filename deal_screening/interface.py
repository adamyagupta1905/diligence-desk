"""Presentation helpers. Domain services do not import this module or Streamlit."""
from .comparison import DocumentComparator


def table(st, rows):
    """A dataframe whose numbers show thousands separators (50,000,000)."""
    rows = list(rows or [])
    numeric = {k for row in rows for k, v in row.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    st.dataframe(rows, hide_index=True,
                 column_config={k: st.column_config.NumberColumn(format="localized") for k in numeric})


def key_values(st, values):
    """Show a flat {name: number} dict as a readable two-column table."""
    rows = [{"item": k.replace("_", " "), "value": "Unknown" if v is None
             else f"{v:,.4g}" if isinstance(v, float) and abs(v) < 10 else
             f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)}
            for k, v in values.items() if not isinstance(v, (dict, list))]
    st.dataframe(rows, hide_index=True)

def show_results(st, state):
    if not state.get("calculations"):
        return
    st.subheader("Python calculations")
    c = state["calculations"]
    is_dcf = c.get("valuation_method") == "dcf"
    currency = state["company"]["currency"]
    cols = st.columns(3)
    for col, label, key in zip(cols, ["Enterprise value", "Equity value", "Historical EBITDA margin" if is_dcf else "EBITDA margin"],
                               ["enterprise_value", "equity_value", "margin"]):
        value = "Unknown" if c.get(key) is None else (f"{c[key]:.1%}" if key == "margin" else f"{currency} {c[key]:,.0f}")
        col.metric(label, value)
    is_dcf = c.get("valuation_method") == "dcf"
    st.caption("Method: enterprise FCFF DCF" if is_dcf else "Method: EV / reported EBITDA multiple")
    if is_dcf:
        if c.get("depreciation_model") == "fixed_assets":
            st.write("Fixed-asset roll-forward — full currency units")
            keys = ("year", "opening_ppe", "ppe_additions", "depreciation_existing_cohorts", "depreciation_current_additions", "depreciation", "closing_ppe", "ppe_rollforward_difference")
            table(st, [{k: r[k] for k in keys} for r in c["projections"]])
            st.caption("Operating cost buckets exclude D&A; scheduled PP&E depreciation is deducted once to reach EBIT and added back once in FCFF. Zero residual values, no disposals/impairments/intangible amortization. New additions timing is a forecast convention. Terminal value still uses normalized g/ROIC reinvestment, not a perpetual continuation of these cohorts.")
        elif c.get("operating_model") == "detailed":
            st.caption("Detailed forecast: operating costs include D&A. EBIT = sales minus costs; EBITDA adds D&A back once. Working capital = receivables + inventory − payables + other operating NWC. Days use 365; this is not a complete forecast balance sheet.")
        st.write("Annual projections — year-end cash flows; full currency units")
        table(st, c["projections"])
        if c.get("three_statements"):
            linked = c["three_statements"]
            st.write("Linked three-statement forecast")
            if linked["status"] != "balanced":
                st.error("Unresolved cash shortfall: balance-sheet cash/assets are withheld in affected years. No automatic borrowing was added. A new input revision with feasible financing is required before approval.")
            st.caption("Income statement taxes include the simplified interest deduction; DCF cash taxes remain unlevered. Interest uses opening debt. Other nonoperating balances are held constant. Book equity is not investment value.")
            for title,key in [("Income statement","income_statement"),("Balance sheet","balance_sheet"),("Cash flow statement","cash_flow_statement"),("Statement link checks","checks")]:
                st.write(title)
                st.dataframe(linked[key],hide_index=True)
            if c.get("stress_three_statements"):
                with st.expander("Downside linked statements and funding needs"):
                    st.json(c["stress_three_statements"])
        st.write("Terminal-value bridge and operating funding gap")
        key_values(st, {k: v for k, v in c.items() if k.startswith("terminal_") or k in {"pv_forecast_fcff", "pv_terminal_value", "remaining_loss_pool", "peak_undiscounted_fcff_deficit"}})
        st.caption("Terminal reinvestment = growth / terminal ROIC. Tax-loss treatment is illustrative. The FCFF deficit is not a financing plan and is not subtracted twice from value.")
    st.write("Sensitivity — WACC and terminal growth" if is_dcf else "Sensitivity — assumed multiples")
    table(st, c["sensitivity"])
    st.write("Downside scenario — lower annual growth/margins, higher WACC" if is_dcf else "Stress scenario — analyst assumptions, net debt held constant")
    stress = {k: v for k, v in c.items() if k.startswith("stress_")}
    key_values(st, stress)
    nested = {k: v for k, v in stress.items() if isinstance(v, (dict, list))}
    if nested:
        with st.expander("Downside detail"):
            st.json(nested)
    for flag in state["flags"]:
        st.warning(flag)
    st.subheader("Draft memo")
    if state.get("memo_error"):
        st.error(state["memo_error"])
    colour = {"S": "blue", "C": "green", "A": "orange", "L": "violet", "D": "violet", "R": "gray"}
    for number, point in enumerate(state.get("memo", {}).get("observations", []), 1):
        with st.container(border=True):
            st.markdown(f"**{number}.** {point['text']}")
            st.markdown(" ".join(f":{colour.get(ref[:1], 'gray')}-badge[{ref}]" for ref in point["references"]))
    questions = state.get("memo", {}).get("diligence_questions", [])
    if questions:
        st.markdown("**Diligence questions**\n" + "\n".join(f"- Diligence question: {q}" for q in questions))
    st.caption("Reference keys: :blue-badge[Sxxx] document passage (see the Evidence tab) · "
               ":green-badge[CALC:key] Python output · :orange-badge[ASSUMPTION:key] analyst assumption · "
               ":gray-badge[RULES] illustrative flags above.")


def show_comparison(st, before, after, before_pages, after_pages):
    st.subheader("Changes from the previous revision")
    st.caption("Text comparison, not legal redlining or Word tracked changes. Extracted facts remain unconfirmed until Checkpoint 1.")
    comparison = DocumentComparator.text_changes(before_pages, after_pages)
    if comparison["diff"]:
        st.code(comparison["diff"], language="diff")
        with st.expander("Page references for text changes"):
            st.json(comparison["changes"])
    else:
        st.info("No line-level text changes. Check whether inputs, assumptions, or evidence selections changed.")
    changes = DocumentComparator.fact_changes(before, after)
    if changes:
        st.dataframe([{"field": c["field"], "category": c["category"],
                       "before": c["before"].get("value"), "after": c["after"].get("value"),
                       "before_status": c["before"].get("status"), "after_status": c["after"].get("status")}
                      for c in changes], hide_index=True)
        with st.expander("Exact evidence and metadata differences"):
            st.json(changes)
    else:
        st.write("No meaningful extracted-fact changes.")
    impact = DocumentComparator.calculation_changes(before, after)
    if impact["available"]:
        st.write("Calculated changes after input confirmation")
        st.dataframe(impact["deltas"], hide_index=True)
        st.caption(impact["note"] + " Currency: " + impact["currency"] + ". Margin changes are decimal ratios.")
        with st.expander("Compare analyst assumptions"):
            st.json({"before": impact["assumptions_before"], "after": impact["assumptions_after"]})
    else:
        st.caption(impact["reason"])
