"""Presentation of explicit valuation assumptions; calculation engines own validation."""
from .dcf import default_dcf
from .operating_forecast import detailed_defaults, COSTS, DAYS
from .statement_forms import statement_inputs


def ratio(value):
    # An emptied editor cell should lead to a validation message on submit,
    # rather than crashing while Streamlit renders the form.
    return value / 100 if isinstance(value, (int, float)) and not isinstance(value, bool) else float("nan")


def choose_method(st, state, revision_id):
    a = state.get("assumptions", {})
    historical = next((f.get("value") for f in state.get("facts", []) if f["field"] == "ebitda"), None)
    if state.get("ledger_enabled") and state.get("ledger_reviewed"):
        historical=next((r["normalized_value"] for r in state.get("ledger_confirmed",[])
                         if r["metric"]=="ebitda" and r["fiscal_year"]==state["ledger_selected_year"]),None)
    try:
        loss = historical is not None and float(historical) <= 0
    except (ValueError, TypeError):
        loss = False
    default = a.get("valuation_method", "dcf" if loss else "ev_ebitda")
    method = st.selectbox("Valuation method", ["ev_ebitda", "dcf"],
        index=1 if default == "dcf" else 0,
        format_func=lambda m: "Enterprise DCF (including loss-making businesses)" if m == "dcf" else "EV / reported EBITDA multiple",
        key="method_" + revision_id)
    years = None
    if method == "dcf":
        st.info("DCF forecasts, WACC, working capital, tax treatment and terminal assumptions are analyst inputs, not extracted facts. Prefilled values are fictional examples to review and replace.")
        years = st.number_input("Forecast years", min_value=3, max_value=10,
                                value=len(a.get("forecast", [])) or 5, step=1, key="horizon_" + revision_id)
        operating_mode = st.selectbox("DCF operating model", ["margin", "detailed"],
            index=1 if a.get("operating_model") == "detailed" else 0,
            format_func=lambda x: "Detailed costs and working-capital days" if x == "detailed" else "Summary EBITDA margin",
            key="dcf_mode_" + revision_id)
        if operating_mode == "detailed":
            asset_selection = st.selectbox("Depreciation model", ["revenue_ratio", "fixed_assets"],
                index=1 if a.get("depreciation_model") == "fixed_assets" else 0,
                format_func=lambda x: "Fixed-asset roll-forward (costs exclude D&A)" if x == "fixed_assets" else "D&A as % of revenue (costs include D&A)",
                key="asset_mode_" + revision_id)
            if asset_selection == "fixed_assets":
                st.checkbox("Build linked three-statement forecast", value=bool(a.get("linked_statements")), key="linked_" + revision_id)
    return method, years


def assumptions_form(st, method, years, state, company):
    a = state.get("assumptions", {})
    if method == "ev_ebitda":
        multiple = st.number_input("Base EV/EBITDA multiple (x)", min_value=0.1, value=float(a.get("multiple", 10.)), step=.5)
        revenue = st.number_input("Stress revenue change (%)", min_value=-99., max_value=200., value=float(a.get("revenue_shock", -.1) * 100))
        margin = st.number_input("Stress EBITDA margin change (percentage points)", min_value=-100., max_value=100., value=float(a.get("margin_shock_pp", -3.)))
        shock = st.number_input("Stress multiple change (x)", min_value=-100., max_value=100., value=float(a.get("multiple_shock", -2.)))
        return {"multiple": multiple, "revenue_shock": revenue / 100,
                "margin_shock_pp": margin, "multiple_shock": shock}
    config = a if a.get("valuation_method") == "dcf" else default_dcf(company)
    mode = st.session_state.get("dcf_mode_" + state.get("revision_id", "standalone"), config.get("operating_model", "margin"))
    asset_mode = st.session_state.get("asset_mode_" + state.get("revision_id", "standalone"), config.get("depreciation_model", "revenue_ratio")) if mode == "detailed" else "revenue_ratio"
    asset_config = None
    if asset_mode == "fixed_assets":
        st.info("Asset-linked mode: enter operating costs EXCLUDING depreciation and amortization. The PP&E schedule supplies depreciation once. Capex is assumed entirely depreciable PP&E; this mode excludes intangible amortization. Review and re-enter costs when switching conventions.")
        previous = config.get("fixed_assets", {})
        candidates = [r for r in state.get("ledger_confirmed", []) if r["metric"] == "ppe" and r["fiscal_year"] == state.get("ledger_selected_year")]
        candidate = candidates[0] if candidates else None
        if candidate:
            st.caption(f"Historical net PP&E reference: {candidate['normalized_value']} {candidate['currency']}; record {candidate['record_id']}. Check depreciable scope (exclude land/CIP) before using it. The opening forecast input below is an analyst-reviewed assumption.")
        else:
            st.caption("No selected-year PP&E reference available. Opening PP&E defaults to zero for review; zero is not an extracted fact.")
        opening = st.number_input("Opening depreciable net PP&E (full currency units)", min_value=0., value=float(previous.get("opening_ppe", candidate["normalized_value"] if candidate else 0.)))
        remaining = st.number_input("Opening PP&E remaining useful life (years)", min_value=1., max_value=100., value=float(previous.get("opening_remaining_life", 5.)))
        life = st.number_input("New PP&E useful life (years)", min_value=1., max_value=100., value=float(previous.get("new_asset_life", 5.)))
        fraction = st.selectbox("New additions depreciation timing", [0., .5, 1.], index=[0., .5, 1.].index(previous.get("first_year_fraction", .5)), format_func=lambda x: {0.: "Start next year", .5: "Half-year in addition year", 1.: "Full-year in addition year"}[x])
        asset_config = {"opening_ppe": opening, "opening_remaining_life": remaining, "new_asset_life": life, "first_year_fraction": fraction}
    elif mode == "detailed":
        st.info("Detailed cost assumptions INCLUDE D&A. EBIT = revenue minus all operating costs; EBITDA = EBIT + D&A. Do not add a separate depreciation expense to these cost rows. Days use a 365-day convention; inventory/payables use cost of sales.")
        st.caption("New detailed values are illustrative assumptions, not automatically mapped historical costs. Review cost scope, working-capital balances and opening NWC against the ledger.")
    st.write("Annual operating forecasts — all percentage columns use percent, e.g. 12 means 12%")
    forecast = detailed_defaults(config["forecast"]) if mode == "detailed" and config.get("operating_model") != "detailed" else config["forecast"]
    rows = []
    for i in range(int(years)):
        row = forecast[min(i, len(forecast) - 1)]
        base = {"year": i + 1, "growth_pct": row["revenue_growth"] * 100,
                "capex_pct": row["capex_ratio"] * 100}
        if asset_mode == "revenue_ratio":
            base["da_pct"] = row.get("da_ratio", .03) * 100
        if mode == "detailed":
            base.update({k.removesuffix("_ratio") + "_pct": row[k] * 100 for k in (*COSTS, "other_nwc_ratio")})
            base.update({k: row[k] for k in DAYS})
        else:
            base.update(ebitda_margin_pct=row.get("ebitda_margin", .20) * 100, nwc_pct=row.get("nwc_ratio", .10) * 100)
        rows.append(base)
    edited = st.data_editor(rows, disabled=["year"], num_rows="fixed", hide_index=True, width="stretch",
                            key="dcf_forecast_" + mode + "_" + asset_mode + "_" + str(years) + "_" + state.get("revision_id", "standalone"))
    wacc = st.number_input("WACC (%)", min_value=.1, max_value=100., value=float(config["wacc"] * 100))
    growth = st.number_input("Terminal growth (%)", min_value=0., max_value=20., value=float(config["terminal_growth"] * 100))
    roic = st.number_input("Terminal ROIC (%)", min_value=.1, max_value=100., value=float(config["terminal_roic"] * 100))
    tax = st.number_input("Cash tax rate (%)", min_value=0., max_value=99., value=float(config["tax_rate"] * 100))
    opening_nwc = st.number_input("Opening noncash working capital (full currency units)", value=float(config["opening_nwc"]))
    opening_nol = st.number_input("Opening loss pool (full currency units; reviewed assumption)", min_value=0., value=float(config["opening_nol"]))
    utilization = st.number_input("Annual loss-pool use limit (% of positive EBIT)", min_value=0., max_value=100., value=float(config["nol_utilization_limit"] * 100))
    st.caption("Simplified operating-loss pool: no refunds, expiry or interest deductions. Terminal cash flow uses full normalized taxes; unused loss benefits are excluded. This does not model a jurisdiction's tax rules.")
    growth_shock = st.number_input("DCF downside annual growth change (percentage points)", min_value=-90., max_value=0., value=float(config["dcf_growth_shock"] * 100))
    margin_shock = st.number_input("DCF downside EBITDA margin change (percentage points)", min_value=-100., max_value=0., value=float(config["dcf_margin_shock"] * 100))
    wacc_shock = st.number_input("DCF downside WACC increase (percentage points)", min_value=0., max_value=50., value=float(config["dcf_wacc_shock"] * 100))
    if mode == "detailed":
        st.caption("All *_pct editor columns use PERCENT: 35 means 35%. Days columns use days. Opening NWC must match the valuation-date operating balances; it excludes cash and debt.")
    forecasts = []
    for row in edited:
        r = {"year": int(row["year"]), "revenue_growth": ratio(row["growth_pct"]), "capex_ratio": ratio(row["capex_pct"])}
        if asset_mode == "revenue_ratio":
            r["da_ratio"] = ratio(row["da_pct"])
        if mode == "detailed":
            r.update({k: ratio(row[k.removesuffix("_ratio") + "_pct"]) for k in (*COSTS, "other_nwc_ratio")})
            r.update({k: row[k] for k in DAYS})
        else:
            r.update(ebitda_margin=ratio(row["ebitda_margin_pct"]), nwc_ratio=ratio(row["nwc_pct"]))
        forecasts.append(r)
    linked = mode == "detailed" and asset_mode == "fixed_assets" and st.session_state.get("linked_" + state.get("revision_id", "standalone"), False)
    statement_config = statement_inputs(st, state, config, forecasts, asset_config) if linked else None
    return {**({"linked_statements": True, "three_statements": statement_config} if linked else {}), **({"depreciation_model": "fixed_assets", "fixed_assets": asset_config} if asset_config else {}), "operating_model": mode, "valuation_method": "dcf", "forecast": forecasts,
            "wacc": wacc / 100, "terminal_growth": growth / 100, "terminal_roic": roic / 100,
            "tax_rate": tax / 100, "opening_nwc": opening_nwc, "opening_nol": opening_nol,
            "nol_utilization_limit": utilization / 100, "dcf_growth_shock": growth_shock / 100,
            "dcf_margin_shock": margin_shock / 100, "dcf_wacc_shock": wacc_shock / 100}

