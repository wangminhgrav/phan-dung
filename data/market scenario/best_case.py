from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix, csr_matrix

INPUT_FILE = "market_scenario_1.csv"
INITIAL_CAPITAL = 10_000.0
PRICE_DIVISOR = 1400.0
LEVERAGE_TIERS = (2.0, 3.0, 4.0)
MAINTENANCE_MARGIN = 0.20
MARGIN_EPS = 0.00001
SURVIVAL_MARGIN = MAINTENANCE_MARGIN + MARGIN_EPS
SETTLEMENT_DELAY = 75
TRADE_TOL = 0
WRITE_MARGIN_AUDIT = True
MILP_TIME_LIMIT_PER_CYCLE = 60.0
MILP_REL_GAP = 0.001
META_COLUMNS = ["Scenario", "Global_Second", "Phase", "Phase_Name", "Phase_Second", "Market_State"]


def locate_input_file() -> Path:
    p = Path(INPUT_FILE)
    if p.exists():
        return p
    matches = sorted(Path(".").glob("market_scenario_1*.csv"))
    if matches:
        print(f"Using {matches[0].name!r}")
        return matches[0]
    raise FileNotFoundError("Put market_scenario_1.csv beside this script.")


def load_market(path: Path):
    df = pd.read_csv(path)
    missing = [c for c in META_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing metadata columns: {missing}")
    tickers = [c for c in df.columns if c not in META_COLUMNS]
    raw = df[tickers].apply(pd.to_numeric, errors="raise").to_numpy(float)
    if np.isnan(raw).any():
        raise ValueError("NaN price found.")
    if (raw <= 0).any():
        r, c = np.argwhere(raw <= 0)[0]
        raise ValueError(f"Non-positive price at row {r + 2}, ticker {tickers[c]!r}.")
    expected = np.arange(1, len(df) + 1)
    if not np.array_equal(df["Global_Second"].to_numpy(), expected):
        raise ValueError("Global_Second must be exactly 1..T.")
    return df, tickers, raw / PRICE_DIVISOR


def timer_display(phase_second: int) -> str:
    remaining = max(0, 300 - int(phase_second))
    return f"{remaining // 60:02d}:{remaining % 60:02d}"


def exact_whole_share_concentrated_dp(price: np.ndarray, tier: float):
    """Exact sequential concentrated DP with integer share quantities."""
    T, A = price.shape
    D = SETTLEMENT_DELAY
    m = SURVIVAL_MARGIN

    cash = np.full(T, -np.inf)
    cash[0] = INITIAL_CAPITAL

    prev_type = np.zeros(T, dtype=np.int8)
    prev_index = np.full(T, -1, dtype=int)
    prev_buy = np.full(T, -1, dtype=int)
    prev_sell = np.full(T, -1, dtype=int)
    prev_asset = np.full(T, -1, dtype=int)
    prev_qty = np.zeros(T, dtype=np.int64)

    terminal_value = INITIAL_CAPITAL
    terminal_trade = None
    terminal_cash_time = 0

    for buy in range(T):
        if buy > 0 and cash[buy - 1] > cash[buy] + 1e-9:
            cash[buy] = cash[buy - 1]
            prev_type[buy] = 1
            prev_index[buy] = buy - 1

        start_wealth = cash[buy]
        if not np.isfinite(start_wealth) or buy + D >= T:
            continue

        p_buy = price[buy]
        future = price[buy:]
        running_min = np.minimum.accumulate(future, axis=0)

        # Initial-margin share cap at the BUY second.
        initial_cap = np.floor((tier * start_wealth + 1e-9) / p_buy)

        # Maintenance: startE + q(Pu-Pb) >= m*q*Pu
        # => q * [Pb - (1-m)Pu] <= startE.
        coeff = p_buy[None, :] - (1.0 - m) * running_min
        maintenance_cap = np.full_like(coeff, np.inf, dtype=float)
        positive_coeff = coeff > 0
        maintenance_cap[positive_coeff] = np.floor(
            (start_wealth + 1e-9) / coeff[positive_coeff]
        )

        q_max = np.minimum(maintenance_cap, initial_cap[None, :])
        q_max = np.maximum(q_max, 0.0)

        wealth = start_wealth + q_max * (future - p_buy[None, :])
        eligible = wealth[D:]
        best_asset = np.argmax(eligible, axis=1)
        rows = np.arange(len(eligible))
        best_wealth = eligible[rows, best_asset]

        # Final trade: final equity counts even if SELL cash has not settled.
        off = int(np.argmax(best_wealth))
        candidate = float(best_wealth[off])
        if candidate > terminal_value + 1e-9:
            sell = buy + D + off
            asset = int(best_asset[off])
            qty = int(q_max[D + off, asset])
            terminal_value = candidate
            terminal_trade = (buy, sell, asset, qty)
            terminal_cash_time = buy

        # Non-final trades need SELL +75 sec before cash can be reused.
        n_chainable = T - (buy + 2 * D)
        if n_chainable <= 0:
            continue

        values = best_wealth[:n_chainable]
        availability = np.arange(buy + 2 * D, T)
        improve = values > cash[availability] + 1e-9
        if not np.any(improve):
            continue

        offsets = np.flatnonzero(improve)
        targets = availability[offsets]
        assets = best_asset[offsets]
        sells = buy + D + offsets
        qtys = q_max[D + offsets, assets].astype(np.int64)

        cash[targets] = values[offsets]
        prev_type[targets] = 2
        prev_buy[targets] = buy
        prev_sell[targets] = sells
        prev_asset[targets] = assets
        prev_qty[targets] = qtys

    if cash[T - 1] > terminal_value + 1e-9:
        terminal_value = float(cash[T - 1])
        terminal_trade = None
        terminal_cash_time = T - 1

    def trace(t: int):
        route = []
        guard = 0
        while t > 0:
            guard += 1
            if guard > T + 10:
                raise RuntimeError("DP predecessor loop detected.")
            if prev_type[t] == 1:
                t = int(prev_index[t])
            elif prev_type[t] == 2:
                route.append((
                    int(prev_buy[t]), int(prev_sell[t]), int(prev_asset[t]), int(prev_qty[t])
                ))
                t = int(prev_buy[t])
            else:
                break
        route.reverse()
        return route

    route = trace(terminal_cash_time)
    if terminal_trade is not None:
        route.append(terminal_trade)
    return terminal_value, route


def optimistic_equity_upper_bound(price: np.ndarray, start: int, last_buy: int, tier: float, start_equity: float):
    """Safe optimistic upper bound used only to give integer variables finite upper bounds."""
    ub = np.empty(last_buy - start + 1)
    ub[0] = start_equity
    for off, u in enumerate(range(start + 1, last_buy + 1), start=1):
        one_tick = price[u] / price[u - 1] - 1.0
        best_positive = max(0.0, float(np.max(one_tick)))
        ub[off] = ub[off - 1] * (1.0 + tier * best_positive)
        if not np.isfinite(ub[off]) or ub[off] > 1e18:
            ub[off:] = 1e18
            break
    return ub


def optimize_staggered_cycle_whole_shares(
    price: np.ndarray,
    start: int,
    sell: int,
    tier: float,
    start_equity: float,
):
    """
    Whole-share staggered re-leveraging MILP for one fixed cycle.

    q[tau,i] is an INTEGER number of shares bought at second tau.
    Every lot is held until the common SELL second.
    """
    if sell - start < SETTLEMENT_DELAY:
        raise ValueError("Cycle shorter than the 75-second BUY settlement requirement.")

    _, A = price.shape
    last_buy = sell - SETTLEMENT_DELAY
    buy_times = np.arange(start, last_buy + 1, dtype=int)

    var_time = np.repeat(buy_times, A)
    var_asset = np.tile(np.arange(A, dtype=int), len(buy_times))
    buy_price = price[var_time, var_asset]
    sell_price = price[sell, var_asset]
    profit_per_share = sell_price - buy_price

    # Safe pruning: if a share never rises above its BUY price before the common exit,
    # it cannot create profit or temporary buying capacity and only consumes margin.
    useful = np.zeros(len(var_time), dtype=bool)
    for k, (tau, i) in enumerate(zip(var_time, var_asset)):
        useful[k] = np.max(price[tau:sell + 1, i]) > price[tau, i] * (1.0 + 1e-12)

    var_time = var_time[useful]
    var_asset = var_asset[useful]
    buy_price = buy_price[useful]
    sell_price = sell_price[useful]
    profit_per_share = profit_per_share[useful]

    if len(var_time) == 0:
        return {
            "end_equity": start_equity,
            "factor": 1.0,
            "allocations": [],
            "equity": np.full(sell - start + 1, start_equity),
            "gross": np.zeros(sell - start + 1),
            "margin_ratio": np.full(sell - start + 1, np.nan),
            "start": start,
            "sell": sell,
            "last_buy": last_buy,
            "solver_status": "no_useful_variables",
            "proven_optimal": True,
            "mip_gap": 0.0,
            "mip_dual_bound": 0.0,
            "n_variables": 0,
            "n_constraints": 0,
        }

    n_vars = len(var_time)

    # Finite but deliberately optimistic individual quantity bounds.
    equity_ub = optimistic_equity_upper_bound(price, start, last_buy, tier, start_equity)
    var_equity_ub = equity_ub[var_time - start]
    quantity_ub = np.floor((tier * var_equity_ub + 1e-9) / buy_price)
    quantity_ub = np.maximum(quantity_ub, 0.0)

    keep = quantity_ub >= 1
    var_time = var_time[keep]
    var_asset = var_asset[keep]
    buy_price = buy_price[keep]
    sell_price = sell_price[keep]
    profit_per_share = profit_per_share[keep]
    quantity_ub = quantity_ub[keep]
    n_vars = len(var_time)

    if n_vars == 0:
        return {
            "end_equity": start_equity,
            "factor": 1.0,
            "allocations": [],
            "equity": np.full(sell - start + 1, start_equity),
            "gross": np.zeros(sell - start + 1),
            "margin_ratio": np.full(sell - start + 1, np.nan),
            "start": start,
            "sell": sell,
            "last_buy": last_buy,
            "solver_status": "no_affordable_variables",
            "proven_optimal": True,
            "mip_gap": 0.0,
            "mip_dual_bound": 0.0,
            "n_variables": 0,
            "n_constraints": 0,
        }

    n_initial_rows = last_buy - start + 1
    n_maintenance_rows = sell - start + 1
    n_rows = n_initial_rows + n_maintenance_rows

    A_ub = lil_matrix((n_rows, n_vars), dtype=float)
    b_ub = np.empty(n_rows, dtype=float)
    row = 0

    # Fixed-tier buying-power constraint while additional BUYs are still allowed:
    # Gross <= tier * Equity.
    for u in range(start, last_buy + 1):
        idx = np.flatnonzero(var_time <= u)
        if len(idx):
            p_u = price[u, var_asset[idx]]
            A_ub[row, idx] = p_u - tier * (p_u - buy_price[idx])
        b_ub[row] = tier * start_equity
        row += 1

    # Maintenance constraint at every second:
    # startE + sum q(Pu-Pb) >= m * sum q*Pu
    # => sum q[Pb-(1-m)Pu] <= startE.
    for u in range(start, sell + 1):
        idx = np.flatnonzero(var_time <= u)
        if len(idx):
            p_u = price[u, var_asset[idx]]
            A_ub[row, idx] = buy_price[idx] - (1.0 - SURVIVAL_MARGIN) * p_u
        b_ub[row] = start_equity
        row += 1

    constraints = LinearConstraint(csr_matrix(A_ub), -np.inf, b_ub)
    bounds = Bounds(np.zeros(n_vars), quantity_ub)

    result = milp(
        c=-profit_per_share,
        integrality=np.ones(n_vars, dtype=int),
        bounds=bounds,
        constraints=constraints,
        options={
            "time_limit": float(MILP_TIME_LIMIT_PER_CYCLE),
            "mip_rel_gap": float(MILP_REL_GAP),
            "presolve": True,
        },
    )

    if result.x is None:
        raise RuntimeError(
            f"Whole-share MILP found no feasible incumbent for start={start + 1}, "
            f"sell={sell + 1}, tier={tier:.0f}x. Solver message: {result.message}"
        )

    q = np.rint(np.maximum(result.x, 0.0)).astype(np.int64)
    end_equity = float(start_equity + np.dot(q, profit_per_share))

    # Reconstruct second-by-second account state.
    equity = np.full(sell - start + 1, start_equity, dtype=float)
    gross = np.zeros(sell - start + 1, dtype=float)

    for off, u in enumerate(range(start, sell + 1)):
        idx = np.flatnonzero((var_time <= u) & (q > 0))
        if len(idx):
            p_u = price[u, var_asset[idx]]
            gross[off] = float(np.dot(q[idx], p_u))
            equity[off] = float(start_equity + np.dot(q[idx], p_u - buy_price[idx]))

    margin_ratio = np.full_like(equity, np.nan)
    active = gross > 1e-12
    margin_ratio[active] = equity[active] / gross[active]

    allocations = []
    for k in np.flatnonzero(q > TRADE_TOL):
        qty = int(q[k])
        tau = int(var_time[k])
        i = int(var_asset[k])
        buy_notional = qty * float(buy_price[k])
        sell_notional = qty * float(sell_price[k])
        allocations.append({
            "Buy_Index": tau,
            "Asset_Index": i,
            "BUY_Quantity": qty,
            "Buy_Price_USD": float(buy_price[k]),
            "BUY_Notional_USD": buy_notional,
            "SELL_Quantity": qty,
            "Sell_Price_USD": float(sell_price[k]),
            "SELL_Value_USD": sell_notional,
            "Profit_USD": sell_notional - buy_notional,
            "Underlying_Return_pct": (float(sell_price[k]) / float(buy_price[k]) - 1.0) * 100.0,
        })

    return {
        "end_equity": end_equity,
        "factor": end_equity / start_equity,
        "allocations": allocations,
        "equity": equity,
        "gross": gross,
        "margin_ratio": margin_ratio,
        "start": start,
        "sell": sell,
        "last_buy": last_buy,
        "solver_status": str(result.message),
        "proven_optimal": bool(result.status == 0),
        "mip_gap": float(getattr(result, "mip_gap", np.nan)),
        "mip_dual_bound": float(getattr(result, "mip_dual_bound", np.nan)),
        "n_variables": n_vars,
        "n_constraints": n_rows,
    }


def run_tier(market_df, tickers, price, tier):
    print("\n" + "=" * 72)
    print(f"FIXED MARGIN TIER: {tier:.0f}x — WHOLE SHARES")
    print("=" * 72)

    baseline_equity, route = exact_whole_share_concentrated_dp(price, tier)
    baseline_multiple = baseline_equity / INITIAL_CAPITAL
    print(f"Whole-share concentrated DP: ${baseline_equity:,.2f} = {baseline_multiple:.6f}x")
    print(f"DP cycles:                   {len(route)}")

    equity = INITIAL_CAPITAL
    cycle_rows, allocation_rows, audit_rows = [], [], []

    for cycle_no, (buy, sell, baseline_asset, baseline_qty) in enumerate(route, start=1):
        start_equity = equity
        baseline_notional = baseline_qty * price[buy, baseline_asset]
        baseline_used_leverage = baseline_notional / start_equity

        print(
            f"  Cycle {cycle_no}: {buy + 1}->{sell + 1}, "
            f"start equity ${start_equity:,.2f}, solving whole-share MILP..."
        )

        model = optimize_staggered_cycle_whole_shares(
            price=price,
            start=buy,
            sell=sell,
            tier=tier,
            start_equity=start_equity,
        )

        equity = model["end_equity"]
        allocations = model["allocations"]
        assets_used = sorted({a["Asset_Index"] for a in allocations})
        valid_mr = model["margin_ratio"][~np.isnan(model["margin_ratio"])]
        min_mr = float(valid_mr.min()) if len(valid_mr) else np.nan
        effective_leverage = np.divide(
            model["gross"],
            model["equity"],
            out=np.zeros_like(model["gross"]),
            where=model["equity"] > 1e-12,
        )

        cycle_rows.append({
            "Tier_x": tier,
            "Cycle": cycle_no,
            "Start_Global_Second": buy + 1,
            "Sell_Global_Second": sell + 1,
            "Last_Allowed_BUY_Second": model["last_buy"] + 1,
            "Baseline_DP_Ticker": tickers[baseline_asset],
            "Baseline_DP_Quantity": baseline_qty,
            "Baseline_DP_Used_Leverage_x": baseline_used_leverage,
            "BUY_Order_Count": len(allocations),
            "Asset_Count": len(assets_used),
            "Starting_Equity_USD": start_equity,
            "Cycle_Growth_x": model["factor"],
            "Ending_Equity_USD": equity,
            "Cumulative_Wealth_x": equity / INITIAL_CAPITAL,
            "Minimum_Margin_Ratio_pct": min_mr * 100.0 if not np.isnan(min_mr) else np.nan,
            "Maximum_Effective_Leverage_x": float(np.max(effective_leverage)),
            "MILP_Proven_Optimal": model["proven_optimal"],
            "MILP_Relative_Gap": model["mip_gap"],
            "MILP_Dual_Bound": model["mip_dual_bound"],
            "MILP_Variables": model["n_variables"],
            "MILP_Constraints": model["n_constraints"],
            "MILP_Status": model["solver_status"],
        })

        for a in allocations:
            i = a["Asset_Index"]
            tau = a["Buy_Index"]
            allocation_rows.append({
                "Tier_x": tier,
                "Cycle": cycle_no,
                "Buy_Global_Second": tau + 1,
                "Sell_Global_Second": sell + 1,
                "Ticker": tickers[i],
                "BUY_Quantity": a["BUY_Quantity"],
                "Buy_Price_USD": a["Buy_Price_USD"],
                "BUY_Notional_USD": a["BUY_Notional_USD"],
                "SELL_Quantity": a["SELL_Quantity"],
                "Sell_Price_USD": a["Sell_Price_USD"],
                "SELL_Value_USD": a["SELL_Value_USD"],
                "Profit_USD": a["Profit_USD"],
                "Underlying_Return_pct": a["Underlying_Return_pct"],
            })

        if WRITE_MARGIN_AUDIT:
            for off, u in enumerate(range(model["start"], model["sell"] + 1)):
                meta = market_df.iloc[u]
                eq = model["equity"][off]
                gross = model["gross"][off]
                mr = model["margin_ratio"][off]
                audit_rows.append({
                    "Tier_x": tier,
                    "Cycle": cycle_no,
                    "Global_Second": int(meta["Global_Second"]),
                    "Phase": int(meta["Phase"]),
                    "Phase_Second": int(meta["Phase_Second"]),
                    "Timer": timer_display(int(meta["Phase_Second"])),
                    "Equity_USD": eq,
                    "Gross_Portfolio_USD": gross,
                    "Effective_Leverage_x": gross / eq if eq > 1e-12 else np.nan,
                    "Margin_Ratio_pct": mr * 100.0 if not np.isnan(mr) else np.nan,
                    "Can_Still_BUY": bool(u <= model["last_buy"]),
                })

        print(
            f"    end ${equity:,.2f} = {equity / INITIAL_CAPITAL:.6f}x | "
            f"orders {len(allocations)} | min MR {min_mr * 100:.4f}% | "
            f"gap {model['mip_gap']:.6%} | optimal={model['proven_optimal']}"
        )

    cycle_df = pd.DataFrame(cycle_rows)
    allocation_df = pd.DataFrame(allocation_rows)
    audit_df = pd.DataFrame(audit_rows)
    final_multiple = equity / INITIAL_CAPITAL
    improvement_pct = (final_multiple / baseline_multiple - 1.0) * 100.0

    summary = {
        "Tier_x": tier,
        "Whole_Shares": True,
        "Concentrated_DP_Final_Equity_USD": baseline_equity,
        "Concentrated_DP_Wealth_x": baseline_multiple,
        "WholeShare_Staggered_Final_Equity_USD": equity,
        "WholeShare_Staggered_Wealth_x": final_multiple,
        "Improvement_vs_DP_pct": improvement_pct,
        "Cycles": len(cycle_df),
        "Total_BUY_Orders": len(allocation_df),
        "Minimum_Margin_Ratio_pct": float(cycle_df["Minimum_Margin_Ratio_pct"].min()) if len(cycle_df) else np.nan,
        "All_Cycles_Proven_Optimal": bool(cycle_df["MILP_Proven_Optimal"].all()) if len(cycle_df) else True,
        "Worst_Cycle_MIP_Gap": float(cycle_df["MILP_Relative_Gap"].max()) if len(cycle_df) else 0.0,
    }

    print(f"Whole-share staggered result: ${equity:,.2f} = {final_multiple:.6f}x")
    print(f"Improvement vs whole-share DP: {improvement_pct:.2f}%")
    return summary, cycle_df, allocation_df, audit_df


def write_outputs(market_df, summary_df, cycles, allocations, audits):
    summary_df.to_csv("whole_share_srlp_summary.csv", index=False)
    cycles.to_csv("whole_share_srlp_cycles.csv", index=False)
    allocations.to_csv("whole_share_srlp_buy_allocations.csv", index=False)
    if WRITE_MARGIN_AUDIT:
        audits.to_csv("whole_share_srlp_margin_audit.csv", index=False)

    with pd.ExcelWriter("whole_share_srlp_output.xlsx", engine="openpyxl") as writer:
        market_df.to_excel(writer, sheet_name="Market Scenario", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        cycles.to_excel(writer, sheet_name="Cycles", index=False)
        allocations.to_excel(writer, sheet_name="BUY Allocations", index=False)
        if WRITE_MARGIN_AUDIT:
            audits.to_excel(writer, sheet_name="Margin Audit", index=False)


def main():
    path = locate_input_file()
    market_df, tickers, price = load_market(path)

    print("=" * 72)
    print("FREE FALL 2.0 — WHOLE-SHARE FIXED-TIER STAGGERED OPTIMIZER")
    print("=" * 72)
    print(f"Input:                {path}")
    print(f"Ticks:                {len(market_df):,}")
    print(f"Tickers:              {len(tickers):,}")
    print(f"Initial capital:      ${INITIAL_CAPITAL:,.2f}")
    print(f"Price divisor:        {PRICE_DIVISOR:g}")
    print(f"T+0.5 delay:          {SETTLEMENT_DELAY} sec")
    print("Allowed margin tiers: 2x, 3x, 4x")
    print("Share rule:           WHOLE SHARES ONLY")
    print(f"Maintenance trigger: <= {MAINTENANCE_MARGIN:.3%}")
    print(f"Survival floor:      >= {SURVIVAL_MARGIN:.3%}")
    print(f"MILP time/cycle:      {MILP_TIME_LIMIT_PER_CYCLE:.0f} sec")
    print(f"MILP target gap:      {MILP_REL_GAP:.3%}")

    summaries, cycle_frames, allocation_frames, audit_frames = [], [], [], []

    for tier in LEVERAGE_TIERS:
        summary, cycles, allocations, audits = run_tier(
            market_df, tickers, price, float(tier)
        )
        summaries.append(summary)
        cycle_frames.append(cycles)
        allocation_frames.append(allocations)
        if WRITE_MARGIN_AUDIT:
            audit_frames.append(audits)

    summary_df = pd.DataFrame(summaries)
    all_cycles = pd.concat(cycle_frames, ignore_index=True)
    all_allocations = pd.concat(allocation_frames, ignore_index=True)
    all_audits = pd.concat(audit_frames, ignore_index=True) if WRITE_MARGIN_AUDIT else pd.DataFrame()

    write_outputs(market_df, summary_df, all_cycles, all_allocations, all_audits)

    print("\n" + "=" * 72)
    print("FINAL WHOLE-SHARE TIER COMPARISON")
    print("=" * 72)
    print(summary_df[[
        "Tier_x",
        "Concentrated_DP_Wealth_x",
        "WholeShare_Staggered_Wealth_x",
        "WholeShare_Staggered_Final_Equity_USD",
        "Improvement_vs_DP_pct",
        "Minimum_Margin_Ratio_pct",
        "All_Cycles_Proven_Optimal",
        "Worst_Cycle_MIP_Gap",
    ]].to_string(index=False))

    best_idx = summary_df["WholeShare_Staggered_Wealth_x"].astype(float).idxmax()
    best = summary_df.loc[best_idx]
    print(f"\nBest fixed tier:       {best['Tier_x']:.0f}x")
    print(f"Best final equity:     ${best['WholeShare_Staggered_Final_Equity_USD']:,.2f}")
    print(f"Best wealth multiple:  {best['WholeShare_Staggered_Wealth_x']:.6f}x")
    print(f"All cycles proven:     {best['All_Cycles_Proven_Optimal']}")
    print(f"Worst cycle MIP gap:   {best['Worst_Cycle_MIP_Gap']:.6%}")

    print("\nCreated:")
    print("  whole_share_srlp_output.xlsx")
    print("  whole_share_srlp_summary.csv")
    print("  whole_share_srlp_cycles.csv")
    print("  whole_share_srlp_buy_allocations.csv")
    if WRITE_MARGIN_AUDIT:
        print("  whole_share_srlp_margin_audit.csv")

    print("\nIMPORTANT:")
    print("Every BUY_Quantity and SELL_Quantity is an integer number of shares.")
    print("If a cycle hits its time limit, its returned portfolio is still feasible,")
    print("but the cycle is not proven optimal unless MILP_Proven_Optimal=True.")


if __name__ == "__main__":
    main()
