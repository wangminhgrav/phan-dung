"""
Free Fall 2.0 - Core Financial Simulation Engine
Author: Nguyen Quang Minh (Technical Developer)
Student ID: 2412380031
Subject: Technology Applications in Banking and Finance (NHA408E)

Updated according to Phase 1 Workflow:
- Bank Savings mechanism (risk-free yield/idle cash storage)
- Settlement delay: T+0.5 holding pen (orders before tick 150 vs after tick 150 pending to Phase 2)
- Margin Call price threshold check
- Forced liquidation penalty deduction
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class MarginStatus(Enum):
    HEALTHY = "HEALTHY"            # Margin Ratio >= Initial Margin Ratio or No Debt
    NORMAL = "NORMAL"              # Maintenance Margin <= Margin Ratio < Initial Margin Ratio
    WARNING = "WARNING"            # Near maintenance threshold (20% <= Margin Ratio < 25%)
    MARGIN_CALL = "MARGIN_CALL"    # Margin Ratio < Maintenance Margin (20%)
    LIQUIDATED = "LIQUIDATED"      # Position liquidated due to margin call


class HouseType(Enum):
    SMALL_HOUSE = "Small House"        # 3.0x Initial Capital
    NORMAL_HOUSE = "Normal House"      # 20.0x Initial Capital
    TOLAM_VILLA = "ToLam Villa"        # 100.0x Initial Capital


HOUSE_MULTIPLIERS = {
    HouseType.SMALL_HOUSE: 3.0,
    HouseType.NORMAL_HOUSE: 20.0,
    HouseType.TOLAM_VILLA: 100.0,
}


@dataclass
class PendingDelivery:
    ticker: str
    shares: float
    delivery_tick: int
    deliver_in_phase: int  # e.g., Phase 2 if purchased after tick 150


@dataclass
class AccountState:
    cash: float
    bank_savings: float = 0.0
    margin_debt: float = 0.0
    holdings: Dict[str, float] = field(default_factory=dict)         # active deliverable shares
    pending_shares: Dict[str, float] = field(default_factory=dict)   # in T+0.5 holding pen or pending phase 2
    pending_queue: List[PendingDelivery] = field(default_factory=list)
    initial_margin_ratio: float = 0.25
    maintenance_margin_ratio: float = 0.20
    liquidation_penalty_pct: float = 0.05  # 5% liquidation penalty fee
    emergency_reserve: float = 0.0
    is_liquidated: bool = False
    margin_call_triggered: bool = False


@dataclass
class FinancialMetrics:
    portfolio_value: float
    bank_savings: float
    margin_debt: float
    cash: float
    equity: float
    leverage: float
    margin_ratio: float
    margin_status: MarginStatus
    target_progress: float
    is_solvent: bool


class MarginEngine:
    """
    Financial Simulation Engine implementing workflow rules:
    - Total Portfolio Value = (Shares * Price) + Bank Savings
    - Equity / Net Worth = Cash + Total Portfolio Value - Margin Debt
    - Margin Ratio = Equity / (Shares * Price)
    """

    def __init__(
        self,
        initial_capital: float,
        target_house_type: HouseType = HouseType.NORMAL_HOUSE,
        initial_margin_ratio: float = 0.25,
        maintenance_margin_ratio: float = 0.20,
        liquidation_penalty_pct: float = 0.05,
    ):
        if initial_capital <= 0:
            raise ValueError("Initial capital must be positive.")
        if initial_margin_ratio <= 0 or initial_margin_ratio > 1.0:
            raise ValueError("Initial margin ratio must be in (0, 1.0].")

        self.initial_capital = initial_capital
        self.target_house_type = target_house_type
        self.target_value = initial_capital * HOUSE_MULTIPLIERS[target_house_type]
        self.initial_margin_ratio = initial_margin_ratio
        self.maintenance_margin_ratio = maintenance_margin_ratio
        self.liquidation_penalty_pct = liquidation_penalty_pct

        self.state = AccountState(
            cash=initial_capital,
            bank_savings=0.0,
            margin_debt=0.0,
            holdings={},
            pending_shares={},
            pending_queue=[],
            initial_margin_ratio=initial_margin_ratio,
            maintenance_margin_ratio=maintenance_margin_ratio,
            liquidation_penalty_pct=liquidation_penalty_pct,
        )

    def deposit_to_savings(self, amount: float) -> bool:
        """Transfer free trading cash into bank savings."""
        if amount <= 0 or amount > self.state.cash:
            return False
        self.state.cash -= amount
        self.state.bank_savings += amount
        return True

    def withdraw_from_savings(self, amount: float, ignore_reserve: bool = False) -> bool:
        """Withdraw funds from bank savings to liquid trading cash."""
        available = self.state.bank_savings if ignore_reserve else (self.state.bank_savings - self.state.emergency_reserve)
        if amount <= 0 or amount > available:
            return False
        self.state.bank_savings -= amount
        self.state.cash += amount
        return True

    def repay_margin_debt(self, amount: float, from_source: str = "bank") -> bool:
        """Directly repay margin debt using bank funds or liquid cash."""
        if amount <= 0 or self.state.margin_debt <= 0:
            return False
        repay_amt = min(amount, self.state.margin_debt)
        if from_source == "bank":
            if repay_amt > self.state.bank_savings:
                return False
            self.state.bank_savings -= repay_amt
            self.state.emergency_reserve = max(0.0, min(self.state.emergency_reserve, self.state.bank_savings))
            self.state.margin_debt -= repay_amt
            return True
        else:
            if repay_amt > self.state.cash:
                return False
            self.state.cash -= repay_amt
            self.state.margin_debt -= repay_amt
            return True

    def set_emergency_reserve(self, amount: float) -> bool:
        """Lock or adjust the emergency reserve portion within bank savings."""
        if amount < 0 or amount > self.state.bank_savings:
            return False
        self.state.emergency_reserve = amount
        return True

    def calculate_max_purchasing_power(self) -> float:
        """Total purchasing power based on liquid cash + equity cushion."""
        if self.state.cash <= 0 or self.state.is_liquidated:
            return 0.0
        return self.state.cash / self.state.initial_margin_ratio

    def set_margin_multiplier(self, multiplier: float) -> float:
        """Sets margin multiplier (1.0x to 4.0x max) and adjusts initial & maintenance margin ratios."""
        multiplier = max(1.0, min(4.0, float(multiplier)))
        self.initial_margin_ratio = 1.0 / multiplier
        self.maintenance_margin_ratio = min(0.20, self.initial_margin_ratio * 0.80)
        self.state.initial_margin_ratio = self.initial_margin_ratio
        self.state.maintenance_margin_ratio = self.maintenance_margin_ratio
        return multiplier

    def calculate_stock_portfolio_value(self, current_prices: Dict[str, float]) -> float:
        """Value of all stock shares (both delivered and in pending delivery)."""
        total = 0.0
        alias_map = {
            "VNT": "Vintrumite", "Vintrumite": "VNT",
            "SEC": "Samsung Electronics", "Samsung Electronics": "SEC",
            "KODEX": "KODEX Semiconductor", "KODEX Semiconductor": "KODEX",
            "KODEX2X": "KODEX Leverage", "KODEX Leverage": "KODEX2X",
            "TIGER": "TIGER Semiconductor TOP10", "TIGER Semiconductor TOP10": "TIGER",
            "HYUNDAI": "Hyundai Motor", "Hyundai Motor": "HYUNDAI",
            "KIA": "Kia", "Kia": "KIA",
            "POSCO-FM": "POSCO Future M", "POSCO Future M": "POSCO-FM",
            "POSCO-HD": "POSCO Holdings", "POSCO Holdings": "POSCO-HD",
            "KZINC": "Korea Zinc", "Korea Zinc": "KZINC",
        }
        # Active holdings
        for ticker, shares in self.state.holdings.items():
            price = current_prices.get(ticker, 0.0)
            if price <= 0.0 and ticker in alias_map:
                price = current_prices.get(alias_map[ticker], 0.0)
            total += shares * price
        # Pending delivery shares (if any remain)
        for ticker, shares in self.state.pending_shares.items():
            price = current_prices.get(ticker, 0.0)
            if price <= 0.0 and ticker in alias_map:
                price = current_prices.get(alias_map[ticker], 0.0)
            total += shares * price
        return total

    def calculate_total_portfolio_value(self, current_prices: Dict[str, float]) -> float:
        """Total Portfolio Value = (Shares * Current_Stock_Price) + Bank Savings."""
        stock_val = self.calculate_stock_portfolio_value(current_prices)
        return stock_val + self.state.bank_savings

    def evaluate(self, current_prices: Dict[str, float]) -> FinancialMetrics:
        if self.state.is_liquidated:
            equity = self.state.cash + self.state.bank_savings - self.state.margin_debt
            return FinancialMetrics(
                portfolio_value=self.state.bank_savings,
                bank_savings=self.state.bank_savings,
                margin_debt=self.state.margin_debt,
                cash=self.state.cash,
                equity=equity,
                leverage=0.0,
                margin_ratio=0.0,
                margin_status=MarginStatus.LIQUIDATED,
                target_progress=max(0.0, equity / self.target_value * 100.0),
                is_solvent=equity > 0,
            )

        stock_portfolio_value = self.calculate_stock_portfolio_value(current_prices)
        total_portfolio_value = stock_portfolio_value + self.state.bank_savings
        cash = self.state.cash
        margin_debt = self.state.margin_debt

        # Equity / Net Worth = Cash + Total Portfolio Value - Margin Debt
        equity = cash + total_portfolio_value - margin_debt

        # Margin Ratio = Equity / (Shares * Current Price)
        if stock_portfolio_value > 0:
            leverage = stock_portfolio_value / equity if equity > 0 else float("inf")
            margin_ratio = equity / stock_portfolio_value
        else:
            leverage = 0.0
            margin_ratio = 1.0

        if stock_portfolio_value == 0 or margin_debt == 0:
            status = MarginStatus.HEALTHY
        elif margin_ratio < self.maintenance_margin_ratio:
            status = MarginStatus.MARGIN_CALL
        elif margin_ratio < self.initial_margin_ratio:
            status = MarginStatus.WARNING
        else:
            status = MarginStatus.NORMAL

        if status == MarginStatus.MARGIN_CALL:
            self.state.margin_call_triggered = True

        target_progress = (equity / self.target_value * 100.0) if self.target_value > 0 else 0.0

        return FinancialMetrics(
            portfolio_value=total_portfolio_value,
            bank_savings=self.state.bank_savings,
            margin_debt=margin_debt,
            cash=cash,
            equity=equity,
            leverage=leverage,
            margin_ratio=margin_ratio,
            margin_status=status,
            target_progress=target_progress,
            is_solvent=equity > 0,
        )

    def execute_order(
        self,
        ticker: str,
        action: str,
        shares: float,
        price: float,
        use_margin: bool = False,
        current_tick: int = 1,
        current_phase: int = 1,
        margin_multiplier: float = None,
    ) -> bool:
        if margin_multiplier is not None and margin_multiplier > 0:
            self.set_margin_multiplier(margin_multiplier)

        if self.state.is_liquidated:
            raise RuntimeError("Account is liquidated. Trading is disabled.")

        action = action.upper()
        if shares <= 0 or price <= 0:
            raise ValueError("Shares and price must be positive.")

        order_cost = shares * price

        if action == "BUY":
            if not use_margin:
                if order_cost > self.state.cash:
                    return False
                self.state.cash -= order_cost
            else:
                mult = self.margin_multiplier if (self.margin_multiplier and self.margin_multiplier > 1.0) else (1.0 / self.initial_margin_ratio)
                required_cash = order_cost / mult
                if self.state.cash < required_cash - 0.001:
                    return False

                new_debt = max(0.0, order_cost - required_cash)
                self.state.cash = max(0.0, self.state.cash - required_cash)
                self.state.margin_debt += new_debt

            self.state.holdings[ticker] = self.state.holdings.get(ticker, 0.0) + shares
            return True

        elif action == "SELL":
            current_shares = self.state.holdings.get(ticker, 0.0)
            if shares > current_shares:
                return False

            proceeds = order_cost
            if self.state.margin_debt > 0:
                repayment = min(self.state.margin_debt, proceeds)
                self.state.margin_debt -= repayment
                proceeds -= repayment

            self.state.cash += proceeds
            self.state.holdings[ticker] -= shares
            if self.state.holdings[ticker] <= 0:
                del self.state.holdings[ticker]
            return True
        else:
            raise ValueError(f"Unknown action: {action}.")

    def process_deliveries(self, current_tick: int, current_phase: int):
        """Processes pending stock deliveries into active holdings when delivery conditions met."""
        remaining_queue = []
        for item in self.state.pending_queue:
            # Deliver if advanced past target phase, or within same phase and tick reached delivery_tick
            should_deliver = (current_phase > item.deliver_in_phase) or (current_phase == item.deliver_in_phase and (current_tick >= item.delivery_tick or item.deliver_in_phase > 1))
            if should_deliver:
                # Deliver stocks to account
                self.state.holdings[item.ticker] = self.state.holdings.get(item.ticker, 0.0) + item.shares
                self.state.pending_shares[item.ticker] -= item.shares
                if self.state.pending_shares[item.ticker] <= 0:
                    del self.state.pending_shares[item.ticker]
            else:
                remaining_queue.append(item)
        self.state.pending_queue = remaining_queue

    def trigger_forced_liquidation(self, current_prices: Dict[str, float]) -> Dict[str, float]:
        """
        System forcefully sells stock shares to repay margin debt.
        Applies liquidation penalty to Cash / Equity.
        """
        gross_proceeds = 0.0
        # Liquidate both delivered holdings and pending shares
        all_shares = dict(self.state.holdings)
        for t, s in self.state.pending_shares.items():
            all_shares[t] = all_shares.get(t, 0.0) + s

        for ticker, shares in all_shares.items():
            price = current_prices.get(ticker, 0.0)
            gross_proceeds += shares * price

        self.state.holdings.clear()
        self.state.pending_shares.clear()
        self.state.pending_queue.clear()

        # Deduct liquidation penalty fee (e.g. 5%)
        penalty = gross_proceeds * self.state.liquidation_penalty_pct
        net_proceeds = max(0.0, gross_proceeds - penalty)

        # Trading cash and net stock liquidation proceeds are used to pay down margin debt
        # IMPORTANT: Bank savings are 100% immune from broker forced liquidation!
        total_available = self.state.cash + net_proceeds
        if total_available >= self.state.margin_debt:
            debt_repaid = self.state.margin_debt
            remaining_funds = total_available - self.state.margin_debt
            self.state.margin_debt = 0.0
            self.state.cash = remaining_funds
        else:
            debt_repaid = total_available
            self.state.margin_debt -= total_available
            self.state.cash = 0.0

        self.state.is_liquidated = True
        final_equity = self.state.cash + self.state.bank_savings - self.state.margin_debt

        return {
            "gross_proceeds": gross_proceeds,
            "penalty": penalty,
            "net_proceeds": net_proceeds,
            "debt_repaid": debt_repaid,
            "remaining_debt": self.state.margin_debt,
            "final_cash": self.state.cash,
            "final_equity": final_equity,
        }

    def get_margin_call_price(self, ticker: str) -> Optional[float]:
        total_shares = self.state.holdings.get(ticker, 0.0) + self.state.pending_shares.get(ticker, 0.0)
        if total_shares <= 0 or self.state.margin_debt <= 0:
            return None
        denom = total_shares * (1.0 - self.maintenance_margin_ratio)
        if denom <= 0:
            return None
        return self.state.margin_debt / denom
