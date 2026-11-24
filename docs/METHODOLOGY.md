# Valuation methodology

All valuation arithmetic is deterministic Python in `deal_screening/`
(`finance.py`, `dcf.py`, `operating_forecast.py`, `fixed_assets.py`,
`three_statements.py`). The language model never calculates. Every assumption
below is an **explicit analyst input** shown on the review form, never an
extracted fact or a hidden default.

The guiding rules:

- **Missing is never zero.** An unknown figure blocks the calculation that
  needs it instead of being silently filled in.
- **No plugs.** There are no balancing adjustments, automatic borrowing, automatic
  FX conversion or invented opening balances.
- **Withhold rather than mislead.** If a result is not meaningful (for example,
  a multiple on non-positive stress EBITDA), it is withheld with a reason.

---

## 1. EV / EBITDA multiple

```
Enterprise value = reported EBITDA × analyst multiple
Equity value     = enterprise value − net debt
```

- Requires **positive, reported** EBITDA (or an explicitly selected, derived
  EBITDA from a reviewed historical ledger). Adjusted EBITDA is not accepted as reported.
- The multiple is an analyst assumption, not a market-derived claim.
- Net cash (negative net debt) increases equity value; a negative equity
  residual is possible and shown.
- **Sensitivity:** the selected multiple ± steps. **Stress case:** revenue
  shock, margin shock (percentage points) and multiple shock, with net debt
  held constant. Stress valuation is withheld if stress EBITDA ≤ 0.

## 2. Enterprise DCF (FCFF)

- 3–10 explicit annual years, **year-end discounting** at a constant WACC.
  Revenue grows from the selected base year.
- `FCFF = EBIT − cash taxes + D&A − capex − Δ non-cash operating working capital`
- Negative early cash flows are kept. Loss-making companies can be valued if
  revenue is positive and the explicit forecast reaches **positive final EBIT and FCFF**.
- **Tax losses:** a simplified operating-loss pool (no refunds, expiry or
  jurisdiction-specific rules). DCF taxes are **unlevered**.
- **Terminal value:** normalized NOPAT on full taxes and the final operating
  margin; reinvestment rate = `g / ROIC`. Requires `0 ≤ g < WACC` and `g < ROIC`.
- **Sensitivity:** a WACC × terminal-growth grid; invalid cells (g ≥ WACC) are
  rejected rather than shown. **Downside:** lower growth and margins and a
  higher WACC; it can be withheld without invalidating the base case.
- **Equity bridge:** subtracts **current** net debt (not forecast debt).
  Dilution is not modelled.
- The peak cumulative FCFF deficit is reported as a **funding diagnostic**. It
  is already inside FCFF and is never subtracted a second time.

## 3. Operating forecast (detailed mode)

- Five cost categories as % of revenue.
  - *Revenue-ratio depreciation mode:* costs **include** D&A, so
    `EBIT = revenue − costs` and `EBITDA = EBIT + D&A`.
  - *Fixed-asset mode:* costs **exclude** D&A, so `EBITDA = revenue − costs`
    and EBIT deducts scheduled depreciation **once**.
- Working capital (365-day convention):
  - receivables = revenue × days / 365
  - inventory and payables = cost of sales × days / 365
  - other operating NWC = revenue × reviewed ratio
  - `NWC = receivables + inventory − payables + other`; the first change uses
    an explicit opening NWC.

## 4. PP&E roll-forward (fixed-asset mode)

- Opening net depreciable PP&E is depreciated over its remaining life.
- Each year's capex forms an independent **straight-line cohort** over a new-asset
  life, with an addition-timing convention of 0, ½ or a full year.
- Zero residual values; charges are capped at the remaining book value.
- `closing PP&E = opening + additions − depreciation`, with a reconciliation check every year.
- Not modelled: land, construction in progress, leases, disposals, impairments,
  revaluations, FX or intangible amortization.

## 5. Linked three-statement forecast (optional)

Requires detailed and fixed-asset modes plus an explicit opening balance sheet
(cash, receivables, inventory, other operating assets, PP&E, other assets,
payables, other operating liabilities, gross debt, other liabilities, total
equity). The opening balance sheet must balance and reconcile with the valuation inputs.

- **Interest** = opening debt × reviewed rate (no circularity; new borrowing
  bears interest from the following year).
- **Income statement:** `pre-tax = EBIT − interest`, with a separate *levered*
  loss pool. Cash taxes equal the P&L charge (no deferred tax).
- **Cash flow:** `CFO = net income + depreciation − ΔNWC`; `CFI = −capex`;
  `CFF = borrowing − repayment + equity issued − dividends`.
- **Balance sheet:** debt follows the explicit financing plan; equity rolls
  forward with net income + issuance − dividends; other non-operating balances stay constant.
- **Checks:** assets = liabilities + equity every year, and
  `CFO + CFI = FCFF − interest + (unlevered tax − levered tax)`.
- **Funding gaps:** an unfunded negative cash trajectory is reported as a
  shortfall, never shown as negative cash. Affected balance-sheet figures are
  withheld and **approval is blocked** until an input revision adds feasible
  financing. Financing changes the statements, not the unlevered EV or WACC.

## 6. Historical ledger

- One to five annual years of income statement, balance sheet and cash-flow
  line items in a controlled taxonomy.
- Each row keeps the raw printed value, scale, sign, exact source quote and a
  normalized `Decimal` amount.
- Conflicting candidates are kept for analyst resolution; exclusions require a note.
- Derived metrics (for example EBITDA = EBIT + D&A, or net debt) are labelled
  *derived*, never *reported*.

## Out of scope

Market-derived WACC builder, detailed tax and deferred tax, revolver and
liquidity modelling, dilution, project-finance debt sculpting and DSCR,
bank or insurer models, and pre-revenue valuation.
