"""What to do about an alert: one sized action, the steps to take and the
figures behind it. Every number is computed here from prices and the trained
models; none is written by a language model."""

import math

from app.ml.features import HORIZON
from app.ml.radar import put_cost_fraction
from app.agents.state import inr as indian_rupees

# Extra downside smaller than this share of the portfolio is not worth a trade.
MIN_TRADE_SHARE = 0.001
STRESS_FALL = 0.05


def inr(amount: float) -> str:
    """Rupees without a sign, in Indian digit grouping."""
    return indian_rupees(abs(amount))


def pct(fraction: float, digits: int = 1) -> str:
    return f"{abs(fraction) * 100:.{digits}f}%"


def solution(action: str, headline: str, steps: list[str | None],
             figures: list[tuple[str, str]] | None = None, alternative: str | None = None, why: str = "",
             orders: list[dict] | None = None, metrics: dict | None = None) -> dict:
    """`action` is one of hedge, trim, rebalance, watch, hold, review. `why` is
    the rule that picked the action, with the numbers it compared. `orders` are
    the trades that carry it out, for the broker; `metrics` are the downside and
    put cost as fractions of the position, for an auto-execution policy."""
    return {"action": action, "headline": headline, "steps": [s for s in steps if s],
            "figures": [{"label": label, "value": value} for label, value in figures or []],
            "alternative": alternative, "why": why, "orders": orders or [], "metrics": metrics or {}}


def sell_order(position: dict, shares: int) -> dict:
    return {"type": "sell", "ticker": position["ticker"], "name": position["name"],
            "quantity": shares, "price": round(position["price"], 2)}


def put_order(position: dict, premium: float, notional: float | None = None) -> dict:
    """An at-the-money put on the holding for the horizon, priced by the estimate used in the alert."""
    return {"type": "buy_put", "ticker": position["ticker"], "name": position["name"],
            "notional": round(notional if notional is not None else position["value"], 2),
            "strike": round(position["price"], 2), "sessions": HORIZON, "premium": round(premium, 2)}


def text(sol: dict) -> str:
    """The solution as one line, for places that show a single sentence."""
    return sol["headline"] + ("." if not sol["headline"].endswith(".") else "") + (
        f' {sol["steps"][0]}' if sol["steps"] else "")


def stop_price(price: float, downside: float) -> float:
    return price * (1 + downside)


def protect_or_trim(position: dict, score: dict, annual_volatility: float, total: float,
                    implied_ratio: float, base_rate: float) -> dict:
    """For a holding the model flags: buy protection when it costs less than the
    downside it removes, otherwise sell enough shares to bring the downside back
    to the holding's usual level, otherwise just set a price alert."""
    value, price, units, name = position["value"], position["price"], position["units"], position["name"]
    loss_now, loss_usual = -score["downside"] * value, -score["usual_downside"] * value
    excess = loss_now - loss_usual
    put_cost = value * put_cost_fraction(annual_volatility * implied_ratio)
    stop = stop_price(price, score["downside"])
    figures = [
        ("Chance of a sharp fall", f'{pct(score["fall"], 0)} (typical day {pct(base_rate, 0)})'),
        (f"{HORIZON}-day downside, 1 in 20", f'{inr(loss_now)} · -{pct(score["downside"])}'),
        ("Its usual downside", f'{inr(loss_usual)} · -{pct(score["usual_downside"])}'),
        (f"Put protection, {HORIZON} sessions", f"about {inr(put_cost)}"),
    ]
    metrics = {"downside": abs(score["downside"]), "put_cost": put_cost / value if value else 0.0}
    alert_step = (f"Set a price alert at ₹{stop:,.2f}: a close below it is a fall the model expects only one "
                  f"week in twenty, and a reason to review the position.")
    shares = min(math.ceil(units * excess / loss_now), math.floor(units)) if loss_now > 0 and excess > 0 else 0

    rule = ("Rule: trade only when the model's downside is larger than the holding's usual downside; then buy "
            "protection if it costs less than the extra downside, otherwise sell enough shares to remove it. ")
    if excess <= 0 or excess < MIN_TRADE_SHARE * total or shares < 1:
        reason = ("The downside is no larger than usual for this stock, so no trade is needed." if excess <= 0 else
                  f"The downside is {inr(excess)} above its usual level, under {pct(MIN_TRADE_SHARE)} of the "
                  f"portfolio, so no trade is needed.")
        why = rule + (
            f"Here the downside is {inr(loss_now)} against a usual {inr(loss_usual)}, which is not larger, so the "
            f"action is to hold and watch." if excess <= 0 else
            f"Here the extra downside is {inr(loss_now)} - {inr(loss_usual)} = {inr(excess)}, below the smallest "
            f"amount worth trading ({pct(MIN_TRADE_SHARE)} of the portfolio, {inr(MIN_TRADE_SHARE * total)}), so "
            f"the action is to hold and watch.")
        return solution("watch", f"Hold {name} and set a price alert at ₹{stop:,.2f}", [reason, alert_step],
                        figures, why=why)

    trim = (f"sell {shares:,} of your {units:,.0f} share{'s' if units != 1 else ''} (about {inr(shares * price)}) "
            f"to bring the downside back to its usual {inr(loss_usual)}")
    if put_cost < excess:
        return solution(
            "hedge", f"Protect {name} with a put, about {inr(put_cost)} for {HORIZON} sessions",
            [f"Buy at-the-money put protection on {inr(value)} of {name}. The estimated cost is {inr(put_cost)}, "
             f"{pct(put_cost / value)} of the position.",
             f"That is less than the {inr(excess)} by which the downside exceeds its usual level, so the "
             f"protection is worth its price.",
             alert_step],
            figures, f"If the stock has no listed options, {trim}.",
            why=rule + f"Here the extra downside is {inr(loss_now)} - {inr(loss_usual)} = {inr(excess)}. A put for "
                       f"{HORIZON} sessions is estimated at {inr(put_cost)}, which is less, so protection is "
                       f"chosen over selling.",
            orders=[put_order(position, put_cost)], metrics=metrics)
    return solution(
        "trim", f"Trim {name} by {shares:,} share{'s' if shares != 1 else ''}, about {inr(shares * price)}",
        [f"{trim[0].upper()}{trim[1:]}.",
         f"Put protection would cost about {inr(put_cost)}, more than the {inr(excess)} of extra downside it "
         f"would cover, so it is not worth buying.",
         alert_step],
        figures,
        why=rule + f"Here the extra downside is {inr(loss_now)} - {inr(loss_usual)} = {inr(excess)}. A put for "
                   f"{HORIZON} sessions is estimated at {inr(put_cost)}, which is more, so selling is chosen: "
                   f"{units:,.0f} shares x {inr(excess)} / {inr(loss_now)}, rounded up, is {shares:,} shares.",
        orders=[sell_order(position, shares)], metrics=metrics)


def hold_after_move(name: str, fell: bool, pattern: str, stop: float | None, large: str | None) -> dict:
    """A sudden move in a holding the model does not flag: the move alone is not a reason to trade."""
    why = ("Rule: a sudden move leads to a trade only when the impact model also flags the holding or it is a "
           "large share of the portfolio. Neither applies here, and past moves of this size continued only about "
           "half the time, so the action is to hold.")
    if not fell:
        return solution("hold", f"No action on {name}",
                        ["A jump does not predict further gains, so there is nothing to chase.", large, pattern],
                        why="Rule: a rise is never a reason to sell or buy by itself; past jumps of this size "
                            "continued only about half the time, so the action is to hold.")
    return solution(
        "hold", f"Hold {name}; do not sell on the move alone",
        ["The size of a drop does not predict what comes next. Check the news for a cause: if it is "
         "company-specific and lasting, review the position; if not, hold.",
         f"Set a price alert at ₹{stop:,.2f}; a close below it would be an unusually bad week." if stop else None,
         pattern], why=why)


def rebalance(largest: dict, target: float, total: float, var_before: float | None, var_after: float | None,
              partners: list[tuple[str, float]], rule: str = "") -> dict:
    """Bring the largest holding down to the target share of the portfolio."""
    amount = largest["value"] - target * total
    price, units = largest.get("price"), largest.get("units")
    figures = [("Share now", pct(largest["value"] / total, 0)), ("Target share", pct(target, 0)),
               ("A 10% fall in it costs", inr(largest["value"] * 0.1))]
    orders = []
    if price and units:
        shares = min(math.ceil(amount / price), math.floor(units))
        orders = [sell_order(largest, shares)] if shares >= 1 else []
        # Whole shares only: the smallest sale may take the holding well under the limit.
        lands = (largest["value"] - shares * price) / total
        headline = (f'Sell {shares:,} share{"s" if shares != 1 else ""} of {largest["name"]}, about '
                    f"{inr(shares * price)}, to bring it to {pct(lands, 0)}"
                    + (f", under the {pct(target, 0)} limit" if target - lands > 0.01 else ""))
    else:
        headline = f'Sell about {inr(amount)} of {largest["name"]} to bring it to {pct(target, 0)}'
    if var_before and var_after:
        figures.append(("1-day 95% VaR", f"{inr(var_before * total)} → {inr(var_after * total)}"))
    return solution(
        "rebalance", headline,
        [f"With the proceeds held as cash, the portfolio's 1-day 95% VaR falls from {inr(var_before * total)} "
         f"to {inr(var_after * total)}." if var_before and var_after else None,
         ("To stay invested, move the proceeds into the holdings that move least with it: "
          + ", ".join(f"{name} (correlation {value:.2f})" for name, value in partners) + ".") if partners else None,
         "If selling would trigger tax you want to avoid, put new money into your other holdings instead "
         "until the share comes down."],
        figures,
        why=f"Rule: bring the largest holding down to the limit for one holding, {pct(target, 0)}"
            f"{f' ({rule})' if rule else ''}. Amount to sell = its value {inr(largest['value'])} - {pct(target, 0)} x "
            f"portfolio value {inr(total)} = {inr(amount)}.",
        orders=orders)


def index_hedge(total: float, beta: float, vix: float | None, headline: str, first_step: str) -> dict:
    """Market-wide risk: the size of an index hedge and what index puts would cost."""
    notional = beta * total
    cost = notional * put_cost_fraction(vix / 100) if vix else None
    figures = [("Portfolio beta", f"{beta:.2f}"),
               (f"A further {pct(STRESS_FALL, 0)} Nifty fall costs", inr(notional * STRESS_FALL)),
               ("Index hedge size", inr(notional))]
    if cost:
        figures.append((f"Index puts, {HORIZON} sessions", f"about {inr(cost)}"))
    return solution(
        "watch", headline,
        [first_step,
         f"If you expect further falls, shorting {inr(notional)} of Nifty 50 futures (beta {beta:.2f} x portfolio "
         f"value) removes the market part of the risk"
         + (f"; index puts on the same amount cost about {inr(cost)} for {HORIZON} sessions at India VIX {vix:.1f}."
            if cost else "."),
         "An index hedge does not cover stock-specific news; the per-holding alerts do that."],
        figures,
        why=f"Rule: market-wide risk is not a reason to sell holdings; it is sized as an index hedge to use only "
            f"if you expect further falls. Hedge size = beta {beta:.2f} x portfolio value {inr(total)} = "
            f"{inr(notional)}."
            + (f" The put cost is a Black-Scholes at-the-money estimate at India VIX {vix:.1f}, not a quote."
               if cost else ""))


def from_hedge(hedge: dict) -> dict:
    """The solution for a weather or geopolitical signal, from the hedge sized for it."""
    figures = [(f'Expected {"loss" if hedge["expected_loss"] >= 0 else "gain"}', inr(hedge["expected_loss"])),
               ("Already priced in", "—" if hedge["priced_in"] is None else pct(hedge["priced_in"], 0)),
               ("Cost of protection", "—" if hedge["cost"] is None else inr(hedge["cost"])),
               ("Signal confidence", hedge["confidence"])]
    steps = [hedge["summary"], hedge.get("liquidity")]
    why = ("Rule: a holding is protected only when the loss still expected on it, after what the market has "
           f"already priced in, is larger than the estimated price of a put on it for {HORIZON} sessions. "
           + hedge["summary"])
    if hedge["action"] == "hedge":
        notional = hedge["notional"] or 1.0
        return solution("hedge", f'{hedge["instrument"]}: protect {inr(hedge["notional"])} for about '
                                 f'{inr(hedge["cost"])}', steps, figures, why=why,
                        # A drill is a rehearsal, so it carries no orders to execute.
                        orders=[] if hedge.get("drill") else hedge.get("orders", []),
                        metrics={"downside": hedge["expected_loss"] / notional, "put_cost": hedge["cost"] / notional,
                                 "confidence": hedge.get("confidence")})
    if hedge["action"] == "monitor":
        return solution("watch", "Do not hedge yet; keep watching", steps, figures, why=why)
    return solution("hold", "No hedge needed", steps, figures, why=why)
