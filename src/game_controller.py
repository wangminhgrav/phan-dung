"""
Free Fall 2.0 - Event and State Controller
Author: Nguyen Quang Minh (Technical Developer)
Student ID: 2412380031
Subject: Technology Applications in Banking and Finance (NHA408E)

This module coordinates multi-phase scenario progression, tick updates from
market_scenario.csv (1,800 seconds across 6 phases, 50 asset tickers),
and user order interactions.
"""

import csv
import json
import os
from typing import Dict, List, Any, Optional
from src.simulation_engine import MarginEngine, HouseType, MarginStatus, FinancialMetrics


class GamePhase:
    def __init__(self, phase_id: int, name: str, description: str, start_second: int, end_second: int):
        self.phase_id = phase_id
        self.name = name
        self.description = description
        self.start_second = start_second
        self.end_second = end_second


class GameController:
    """
    Controls overall game progression using the realistic market_scenario.csv
    (50 tickers across 1,800 global seconds) with fallback to JSON.
    """

    def __init__(
        self,
        scenario_csv_path: str = "market scenario/market_scenario.csv",
        initial_capital: float = 10000.0,  # $10,000 USD default as in calibrated backtest
        target_house_type: HouseType = HouseType.NORMAL_HOUSE,
        initial_margin_ratio: float = 0.25,
        maintenance_margin_ratio: float = 0.20,
    ):
        self.engine = MarginEngine(
            initial_capital=initial_capital,
            target_house_type=target_house_type,
            initial_margin_ratio=initial_margin_ratio,
            maintenance_margin_ratio=maintenance_margin_ratio,
        )
        self.current_phase_index = 0
        self.phases: List[GamePhase] = []
        self.price_ticks: List[Dict[str, Any]] = []
        self.current_prices: Dict[str, float] = {}
        self.current_tick_index = 0
        self.history: List[Dict[str, Any]] = []

        if os.path.exists(scenario_csv_path):
            self._load_from_csv(scenario_csv_path)
        else:
            self._load_from_json("data/market_scenario.json")

    def _load_from_csv(self, csv_path: str):
        with open(csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            meta_cols = {"Scenario", "\ufeffScenario", "Global_Second", "Phase", "Phase_Name", "Phase_Second", "Market_State"}
            tickers = [col for col in reader.fieldnames if col not in meta_cols]

            is_usd_mode = self.engine.initial_capital <= 50000.0
            phase_seen = {}
            for row in reader:
                if not row.get("Global_Second") or not str(row["Global_Second"]).strip():
                    continue
                if not row.get("Phase") or not str(row["Phase"]).strip():
                    continue
                g_sec = int(row["Global_Second"])
                p_id = int(row["Phase"])
                p_name = row["Phase_Name"]

                prices = {}
                for t in tickers:
                    if row[t] != "":
                        raw_p = float(row[t])
                        if is_usd_mode:
                            if t == "Vintrumite":
                                prices[t] = round(raw_p / 10000.0, 2)
                            else:
                                prices[t] = round(raw_p / 1400.0, 2)
                        else:
                            prices[t] = raw_p
                ticker_alias_map = {
                    "VNT": "Vintrumite",
                    "000660": "Vintrumite",
                    "SEC": "Samsung Electronics",
                    "005930": "Samsung Electronics",
                    "KODEX": "KODEX Semiconductor",
                    "091160": "KODEX Semiconductor",
                    "KODEX2X": "KODEX Leverage",
                    "122630": "KODEX Leverage",
                    "TIGER": "TIGER Semiconductor TOP10",
                    "396500": "TIGER Semiconductor TOP10",
                    "HYUNDAI": "Hyundai Motor",
                    "005380": "Hyundai Motor",
                    "KIA": "Kia",
                    "000270": "Kia",
                    "POSCO-FM": "POSCO Future M",
                    "003670": "POSCO Future M",
                    "POSCO-HD": "POSCO Holdings",
                    "005490": "POSCO Holdings",
                    "KZINC": "Korea Zinc",
                    "010130": "Korea Zinc",
                }
                for code, full_name in ticker_alias_map.items():
                    if full_name in prices:
                        prices[code] = prices[full_name]

                self.price_ticks.append({
                    "global_second": g_sec,
                    "phase": p_id,
                    "phase_name": p_name,
                    "prices": prices,
                })

                if p_id not in phase_seen:
                    phase_seen[p_id] = {
                        "name": p_name,
                        "start": g_sec,
                        "end": g_sec,
                    }
                else:
                    phase_seen[p_id]["end"] = g_sec

            phase_descs = {
                1: "Initial trading session. Review pre-game briefing and establish positions.",
                2: "Second trading session. Economic reports and corporate developments unfold.",
                3: "Third trading session. Market momentum and asset trends develop.",
                4: "Fourth trading session. Mid-game market dynamics and intelligence wires stream.",
                5: "Fifth trading session. Late-cycle market action demands heightened solvency awareness.",
                6: "Final trading session. 30-minute simulation concludes with a solvency audit.",
            }

            for p_id in sorted(phase_seen.keys()):
                info = phase_seen[p_id]
                self.phases.append(
                    GamePhase(
                        phase_id=p_id,
                        name=f"Phase {p_id}",
                        description=phase_descs.get(p_id, ""),
                        start_second=info["start"],
                        end_second=info["end"],
                    )
                )

        if self.price_ticks:
            self.current_prices = dict(self.price_ticks[0]["prices"])

    def _load_from_json(self, json_path: str):
        with open(json_path, mode="r", encoding="utf-8") as f:
            data = json.load(f)
        self.current_prices = data.get("base_prices", {})
        for p in data.get("phases", []):
            self.phases.append(
                GamePhase(
                    phase_id=p["phase_id"],
                    name=p["name"],
                    description=p["description"],
                    start_second=(p["phase_id"] - 1) * 300 + 1,
                    end_second=p["phase_id"] * 300,
                )
            )

    def get_current_phase(self) -> GamePhase:
        return self.phases[self.current_phase_index]

    def set_tick_by_global_second(self, second: int) -> Dict[str, float]:
        idx = max(0, min(second - 1, len(self.price_ticks) - 1))
        self.current_tick_index = idx
        tick_data = self.price_ticks[idx]
        self.current_prices = dict(tick_data["prices"])
        target_phase = tick_data["phase"]
        for i, p in enumerate(self.phases):
            if p.phase_id == target_phase:
                self.current_phase_index = i
                break
        return self.current_prices

    def player_trade(
        self,
        ticker: str,
        action: str,
        shares: float,
        use_margin: bool = False,
    ) -> bool:
        price = self.current_prices.get(ticker, 0.0)
        if price <= 0:
            return False
        return self.engine.execute_order(ticker, action, shares, price, use_margin)

    def advance_phase(self) -> Dict[str, Any]:
        """
        Advances to the next phase end-state tick.
        """
        if self.current_phase_index + 1 < len(self.phases):
            self.current_phase_index += 1
            phase = self.phases[self.current_phase_index]
            self.set_tick_by_global_second(phase.end_second)

        metrics = self.engine.evaluate(self.current_prices)

        liquidation_info = None
        if metrics.margin_status == MarginStatus.MARGIN_CALL:
            liquidation_info = self.engine.trigger_forced_liquidation(self.current_prices)
            metrics = self.engine.evaluate(self.current_prices)

        record = {
            "phase_id": self.phases[self.current_phase_index].phase_id,
            "phase_name": self.phases[self.current_phase_index].name,
            "metrics": metrics,
            "liquidation_info": liquidation_info,
        }
        self.history.append(record)
        return record

    def get_summary(self) -> Dict[str, Any]:
        metrics = self.engine.evaluate(self.current_prices)
        return {
            "initial_capital": self.engine.initial_capital,
            "target_house_type": self.engine.target_house_type.value,
            "target_value": self.engine.target_value,
            "final_equity": metrics.equity,
            "final_cash": metrics.cash,
            "final_margin_debt": metrics.margin_debt,
            "target_progress": metrics.target_progress,
            "target_reached": metrics.equity >= self.engine.target_value,
            "is_liquidated": self.engine.state.is_liquidated,
            "is_solvent": metrics.is_solvent,
            "history_length": len(self.history),
        }
