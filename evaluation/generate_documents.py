"""Generate 20 synthetic infrastructure annual reviews plus an answer key.

Everything is FICTIONAL and generated from a fixed random seed, so the same
documents come out every time. Each document mixes:
  - synonyms ("turnover", "borrowings less cash", "operating profit before D&A")
  - decoys (adjusted EBITDA, prior-year turnover, market growth, gross debt,
    staff turnover, EBITDA margin targets)
  - hard cases: figures in thousands (the scale is stated on another page),
    net cash instead of net debt, adjusted-EBITDA-only (reported EBITDA must
    come back missing), and optional facts that are simply absent.

The answer key stores, for every field, the true value in full units and a
"needle": the exact text of the sentence that proves it.

From the repository root:
    .venv/bin/python evaluation/generate_documents.py
"""
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "documents"
SEED = 2026
N_DOCS = 20

CURRENCIES = [("USD", "US dollars"), ("EUR", "euros"), ("GBP", "pounds sterling"),
              ("CAD", "Canadian dollars"), ("AUD", "Australian dollars")]
SECTORS = [
    ("Solara Grid", "develops and operates utility-scale solar farms that sell electricity under long-term power purchase agreements"),
    ("Northway Tollroads", "operates tolled motorways and bridges under long-term government concessions"),
    ("Clearbrook Water", "supplies drinking water and treats wastewater for regulated municipal customers"),
    ("Meridian Ports", "runs container and bulk terminals at three deep-water harbours"),
    ("Corelink Data Centres", "builds and leases powered data-centre capacity to cloud and enterprise tenants"),
    ("Heatwell District Energy", "owns district heating networks that supply hot water to homes and offices"),
    ("Ironline Rail Freight", "operates rail freight terminals and wagon fleets for bulk and intermodal cargo"),
    ("Skyharbour Airports", "owns and operates two regional airports and their retail concessions"),
    ("Gale Point Wind", "owns onshore and offshore wind farms with contracted and merchant revenue"),
    ("Brightfibre Networks", "builds and wholesales full-fibre broadband networks to internet providers"),
    ("Keystone Pipelines", "transports natural gas through regulated high-pressure pipelines"),
    ("Renew Cycle Energy", "converts household waste into electricity and heat at energy-from-waste plants"),
    ("VoltWay Charging", "installs and operates rapid electric-vehicle charging hubs"),
    ("Steadfast Storage", "operates grid-scale battery storage sites that provide balancing services"),
    ("Riverfall Hydro", "owns run-of-river hydroelectric plants with long-dated offtake contracts"),
    ("Coastline Desalination", "operates seawater desalination plants under availability-based contracts"),
    ("Metroline Transit", "operates light-rail systems under public-private partnership agreements"),
    ("Granite Towers", "owns telecom towers and leases space to mobile network operators"),
    ("Evergreen Biogas", "produces renewable gas from agricultural waste and injects it into the grid"),
    ("Harbour Cold Chain", "runs temperature-controlled logistics hubs at major ports"),
]
SUFFIX = ["plc", "Ltd", "Inc.", "Group plc", "Holdings Ltd"]


def money(value, units, word):
    """Format a full-unit amount the way the document prints it."""
    if units == "thousands":
        return f"{value / 1000:,.0f}"
    return f"{value / 1e6:,.1f} million {word}"


def make_case(i, rng):
    base, business = SECTORS[i]
    name = f"{base} {rng.choice(SUFFIX)}"
    iso, word = rng.choice(CURRENCIES)
    year = rng.choice([2024, 2025])
    end = rng.choice([("31 December", f"year ended 31 December {year}"),
                      ("30 June", f"twelve months ended 30 June {year}"),
                      ("31 March", f"financial year ended 31 March {year}")])
    period_long = end[1]
    units = "thousands" if i % 4 == 3 else "millions"
    revenue = rng.randrange(800, 9000) * 100_000            # 80m–900m, one decimal of a million
    growth = rng.randrange(2, 19)
    margin = rng.randrange(25, 62) / 100
    ebitda = round(revenue * margin / 100_000) * 100_000
    net_cash = i in (5, 14)
    net = rng.randrange(50, 30_000) * 100_000 if not net_cash else rng.randrange(50, 900) * 100_000
    flags = {
        "adjusted_only": i in (7, 16),
        "adjusted_decoy": i % 3 == 0,
        "growth_present": i not in (4, 11),
        "concentration_present": i % 4 != 1,
        "net_cash": net_cash,
        "units": units,
    }
    concentration = rng.randrange(12, 45)
    fmt = lambda v: money(v, units, word)

    facts = {}
    sentences = {}
    s = rng.choice(["The registered entity is {n}.", "{n} is the parent company of the group.",
                    "{n} (the Company) heads the group described in this review."]).format(n=name)
    sentences["company_name"] = s
    facts["company_name"] = {"value": base, "needle": s}
    s = f"The group {business}."
    sentences["business"] = s
    facts["business"] = {"value": None, "needle": s, "free_text": True}
    if units == "thousands":
        s = f"All amounts in this review are presented in thousands of {word} unless otherwise stated."
    else:
        s = f"All amounts in this review are presented in {word}."
    sentences["currency"] = s
    facts["currency"] = {"value": iso, "needle": s}
    s = f"These results cover the {period_long}."
    sentences["period"] = s
    facts["period"] = {"value": str(year), "needle": s}

    s = rng.choice(["Turnover for the period was {a}.", "Total income from operations reached {a}.",
                    "Revenues amounted to {a} for the period.", "The group generated sales of {a} during the period."]
                   ).format(a=fmt(revenue))
    sentences["revenue"] = s
    facts["revenue"] = {"value": str(revenue), "needle": s}

    if flags["growth_present"]:
        s = rng.choice(["This was an increase of {g}% on the prior year.", "That was {g}% higher than the previous year.",
                        "Income was up {g}% year on year."]).format(g=growth)
        sentences["revenue_growth"] = s
        facts["revenue_growth"] = {"value": f"{growth / 100:.2f}", "needle": s}
    else:
        facts["revenue_growth"] = {"value": None, "needle": None}

    adjusted = ebitda + rng.randrange(20, 80) * 100_000
    if flags["adjusted_only"]:
        sentences["ebitda"] = f"Adjusted EBITDA, which excludes one-off restructuring costs, was {fmt(adjusted)}."
        facts["ebitda"] = {"value": None, "needle": None}
    else:
        s = rng.choice(["Operating profit before depreciation and amortisation was {a}.",
                        "EBITDA for the period was {a}.",
                        "Earnings before interest, tax, depreciation and amortisation totalled {a}."]).format(a=fmt(ebitda))
        if flags["adjusted_decoy"]:
            s2 = f" Adjusted EBITDA, which excludes one-off restructuring costs, was {fmt(adjusted)}."
        else:
            s2 = ""
        sentences["ebitda"] = s + s2
        facts["ebitda"] = {"value": str(ebitda), "needle": s}

    if net_cash:
        s = f"At {end[0]} {year}, cash exceeded borrowings, giving a net cash position of {fmt(net)}."
        facts["net_debt"] = {"value": str(-net), "needle": s}
    else:
        s = rng.choice(["Net debt at {d} was {a}.", "Borrowings less cash and cash equivalents stood at {a} at {d}.",
                        "Net borrowings at {d} were {a}."]).format(a=fmt(net), d=f"{end[0]} {year}")
        facts["net_debt"] = {"value": str(net), "needle": s}
    sentences["net_debt"] = s

    if flags["concentration_present"]:
        s = rng.choice(["Our largest customer accounted for {c}% of revenue.",
                        "The single biggest client represented {c}% of turnover.",
                        "{c}% of income came from one offtaker under a long-term contract."]).format(c=concentration)
        sentences["customer_concentration"] = s
        facts["customer_concentration"] = {"value": f"{concentration / 100:.2f}", "needle": s}
    else:
        facts["customer_concentration"] = {"value": None, "needle": None}

    prior = round(revenue / (1 + growth / 100) / 100_000) * 100_000
    gross = (net if not net_cash else 0) + rng.randrange(100, 900) * 100_000
    return name, base, word, year, flags, facts, sentences, fmt, prior, gross


def pages_for(name, base, word, year, flags, sentences, fmt, prior, gross, rng):
    S = sentences
    g = lambda key: S.get(key, "")
    filler = {
        "chairman": ["CHAIRMAN'S LETTER",
                     f"It has been a year of steady progress for {base}. The board met regularly throughout the fiscal year to review strategy, safety and capital allocation.",
                     "We welcomed two new independent directors, and the audit committee completed a full review of internal controls. The board expects the coming fiscal year to bring further investment."],
        "people": ["OUR PEOPLE",
                   "We employ around 1,800 colleagues across operations, engineering and support functions. Staff turnover fell during the year after we introduced new shift patterns and a retention programme.",
                   "Safety remains our first priority, and the lost-time injury rate improved again. Employee engagement scores rose for the third consecutive year."],
        "market": ["MARKET CONTEXT",
                   f"Demand for essential infrastructure remains resilient. Industry analysts expect market growth of around {rng.randrange(2, 7)}% per year over the medium term, supported by population growth and decarbonisation policy.",
                   "Competition for high-quality assets is intense, and inflation-linked contracts provide some protection against rising costs."],
        "risk": ["RISK MANAGEMENT",
                 "Principal risks include regulatory change, construction delays, counterparty failure, cyber attacks, interest rates and currency movements.",
                 "Currency risk: some equipment is purchased from overseas suppliers. Our currency hedging policy allows forward contracts for up to eighteen months of committed purchases. We do not speculate on currency."],
        "treasury": ["TREASURY AND FUNDING",
                     f"The group is funded through long-dated bonds and a committed revolving credit facility. Gross borrowings at the year end were {fmt(gross)}, with an average maturity of nine years.",
                     "Covenants were met with comfortable headroom. Surplus cash is held in instant-access deposits with highly rated banks."],
        "policies": ["ACCOUNTING POLICIES",
                     "Depreciation is charged on a straight-line basis over the useful life of each asset, between 5 and 40 years. Depreciation and amortisation methods are reviewed each year.",
                     "Revenue recognition: contracted income is recognised as the service is delivered. Grants are recognised over the life of the related asset."],
        "governance": ["GOVERNANCE",
                       "The board comprises a non-executive chair, two executive directors and five independent non-executive directors. Each committee met at least four times during the fiscal year.",
                       "Directors' remuneration is linked to safety, availability, cash generation and long-term returns."],
        "sustainability": ["SUSTAINABILITY",
                           "We aim to reach net zero across our own operations by 2040. Emissions intensity fell this year as we switched to renewable electricity at all major sites.",
                           "We publish our full climate disclosures online and report against recognised frameworks."],
        "outlook": ["OUTLOOK",
                    f"We target an EBITDA margin of {rng.randrange(40, 70)}% by {year + 3} as new assets reach full operation.",
                    "Our pipeline of committed projects underpins growth, although timing depends on permits and grid connections."],
        "history": ["FIVE-YEAR PERSPECTIVE",
                    f"In the prior year, turnover was {fmt(prior)}. Over five years the asset base has more than doubled through construction and selective acquisitions.",
                    "We have increased our dividend every year since listing."],
    }
    facts_pages = [
        ["WHO WE ARE", g("company_name"), g("business"),
         "Our assets are long-lived and most of our income is contracted or regulated."],
        ["BASIS OF PREPARATION", "This fictional review has been prepared for a document retrieval exercise.",
         g("currency"), g("period")],
        ["RESULTS FOR THE PERIOD", g("revenue") + (" " + g("revenue_growth") if g("revenue_growth") else ""),
         g("ebitda"), "Capital expenditure focused on new assets and maintenance."],
        ["FINANCIAL POSITION", g("net_debt"), "Liquidity remains strong, and no major refinancing is due within two years."],
    ]
    if g("customer_concentration"):
        facts_pages.append(["CUSTOMERS AND CONTRACTS", g("customer_concentration"),
                            "Contract renewals are staggered, and counterparties are regularly reviewed for credit quality."])
    keys = list(filler)
    rng.shuffle(keys)
    chosen = [filler[k] for k in keys[:rng.randrange(6, 10)]]
    body = chosen + facts_pages
    rng.shuffle(body)
    cover = [f"ANNUAL REVIEW — {name.upper()}", "FICTIONAL EXAMPLE FOR EVALUATION — NOT A REAL COMPANY.",
             "This review describes our assets, people, risks and results."]
    return [cover] + body


def main():
    rng = random.Random(SEED)
    OUT.mkdir(exist_ok=True)
    key = []
    for i in range(N_DOCS):
        name, base, word, year, flags, facts, sentences, fmt, prior, gross = make_case(i, rng)
        pages = pages_for(name, base, word, year, flags, sentences, fmt, prior, gross, rng)
        filename = f"eval_{i + 1:02d}_{base.lower().replace(' ', '_')}.txt"
        text = "\f".join("\n\n".join(p for p in page if p) + "\n" for page in pages)
        (OUT / filename).write_text(text)
        key.append({"file": filename, "flags": flags, "facts": facts})
    (HERE / "answer_key.json").write_text(json.dumps(key, indent=2))
    print(f"Wrote {N_DOCS} documents to {OUT} and answer_key.json")


if __name__ == "__main__":
    main()
