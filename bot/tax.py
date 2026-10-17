"""Israeli capital-gains overlay for a report. This does not change a pre-tax gate.

25% of a calendar year's net realized gain. A loss in that year offsets a gain. A year that
nets to a loss pays nothing and does not carry forward. There is no refund. The tax is
subtracted from terminal equity once. It is not compounded back into the path.
"""
TAX = 0.25


def annual_tax(realizations):
    """`(date, pnl)` pairs. One row per year that had a realization: year, net, tax."""
    by_year = {}
    for day, pnl in realizations or ():
        year = str(day)[:4]
        by_year[year] = by_year.get(year, 0.0) + float(pnl)
    rows = []
    for year in sorted(by_year):
        net = by_year[year]
        rows.append({"year": year, "net": net, "tax": TAX * net if net > 0 else 0.0})
    return rows


def after_tax_terminal(pre_tax_terminal, realizations):
    """Terminal equity after subtracting each year's tax. The drag is not compounded."""
    years = annual_tax(realizations)
    tax = sum(row["tax"] for row in years)
    return pre_tax_terminal - tax, tax, years


def spy_hold_tax(start_eq, end_eq):
    """Buy-and-hold taxed once, on the gain from the first dollar to the last dollar."""
    gain = end_eq - start_eq
    tax = TAX * gain if gain > 0 else 0.0
    return end_eq - tax, tax
