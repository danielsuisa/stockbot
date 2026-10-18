"""IBKR Pro US-stock costs per order (ADR 0005 section 8), from
https://www.interactivebrokers.com/en/pricing/commissions-stocks.php (read 2026-10-18).

Tiered: $0.0035/share (<= 300k shares/month), min $0.35, max 1% of trade value (applied even below the minimum);
clearing $0.00020/share; NYSE and FINRA pass-through 0.000175 and 0.000565 x commission; exchange fee $0.0030/share
(Proposed: Reg NMS access-fee cap, conservative). Fixed: $0.005/share, min $1.00, max 1% of trade value, exchange and
clearing included. Both: FINRA CAT $0.000003/share; on sales the SEC fee 0.0000206 x value and FINRA TAF
$0.000166/share (max $8.30) before 2026-10-01, $0 from then (fee holiday; pre-holiday rate Proposed)."""
import datetime as dt

TAF_HOLIDAY = dt.date(2026, 10, 1)


def commission(plan, q, price):
    value = q * price
    if plan == "tiered":
        c = max(0.0035 * q, 0.35)
    elif plan == "fixed":
        c = max(0.005 * q, 1.00)
    else:
        raise ValueError(plan)
    return min(c, 0.01 * value)


def order_cost(plan, side, q, price, day):
    """Commission plus every fee for one order; q > 0 shares at price, side 'buy' or 'sell'."""
    if q <= 0:
        return 0.0
    c = commission(plan, q, price)
    total = c + 0.000003 * q  # FINRA CAT
    if plan == "tiered":
        total += 0.0030 * q + 0.00020 * q + c * (0.000175 + 0.000565)
    if side == "sell":
        total += 0.0000206 * q * price
        if day < TAF_HOLIDAY:
            total += min(0.000166 * q, 8.30)
    return total
