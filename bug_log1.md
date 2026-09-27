# BUG TRIAGE AND RESOLUTION LOG

**Project:** Free Fall 2.0  
**Phase:** Week 6 — Build, Integration and Testing Clinic  
**Focus:** Operational Stability, Edge-case Handling & Deployment Resilience  

---

## 1. Overview and Severity Standards

This bug log tracks defect discovery, root cause diagnosis, code resolution, and verification across the integrated simulation engine, client interface, and data transport layers.

### Severity Classification Standard
- **CRITICAL:** Core workflow blocked, wrong financial calculation/logic, deployment failure, or data loading crash.
- **MAJOR:** Important secondary feature path failure, missing explanation/feedback, unhandled edge input.
- **MINOR:** Cosmetic inconsistency, UI spacing/styling artifact, or non-blocking repo organization issues.

### Lifecycle Statuses
`NEW` $\rightarrow$ `CONFIRMED` $\rightarrow$ `IN_PROGRESS` $\rightarrow$ `FIXED` $\rightarrow$ `VERIFIED`

---

## 2. Bug Master Table

| Bug ID | Severity | Component | Issue Summary | Status | Reported By | Fixed By | Verified By |
|:---:|:---:|---|---|:---:|:---:|:---:|:---:|
| **BUG-01** | **CRITICAL** | Engine (`simulation_engine.py`) | Orders placed after Tick 150 failed to deliver upon Phase 2 transition | **VERIFIED** | Financial Analyst | Backend Dev | QA Lead |
| **BUG-02** | **CRITICAL** | Engine (`simulation_engine.py`) | Uninitialized `margin_multiplier` attribute caused crash during BUY orders | **VERIFIED** | QA Lead | Core Dev | Team Tester |
| **BUG-03** | **MAJOR** | UI / Engine | Floating point division produced trailing decimal artifacts in Margin Ratio | **VERIFIED** | Team Tester | Frontend Dev | Financial Analyst |
| **BUG-04** | **MAJOR** | Engine / API | Post-liquidation trading attempts produced unhandled state corruption | **VERIFIED** | QA Lead | Core Dev | Financial Analyst |
| **BUG-05** | **MAJOR** | Engine Valuation | Margin Ratio for pure cash positions displayed 200% instead of capped 100% | **VERIFIED** | Financial Analyst | Backend Dev | QA Lead |
| **BUG-06** | **MINOR** | Repository Root | Loose background PNG images cluttered repository root folder | **VERIFIED** | Team Tester | Repo Admin | QA Lead |

---

## 3. Detailed Bug Diagnostic Reports

### BUG-01: Intra-phase Delivery Queue Reset on Tick Advancement
- **Bug ID:** `BUG-01`
- **Severity:** `CRITICAL` (Breaks T+0.5 settlement workflow)
- **Component:** Core Simulation Engine (`src/simulation_engine.py`, `process_deliveries`)
- **Reported By:** Financial Analyst | **Fixed By:** Backend Dev | **Verified By:** QA Lead
- **What Happened:** Orders placed after Tick 150 were assigned `deliver_in_phase = 2`. However, upon entering Phase 2, the delivery check required both `current_phase > deliver_in_phase` AND `current_tick >= delivery_tick`. Because `current_tick` reset to 1 at the start of Phase 2, shares remained trapped in `pending_queue`.
- **Steps to Reproduce:**
  1. Execute BUY order at Tick 200 of Phase 1.
  2. Verify shares are in `pending_shares`.
  3. Advance engine to Phase 2, Tick 1.
  4. Check `holdings` balance.
- **Actual Behavior:** `holdings` remained at 0 shares; pending deliveries did not convert.
- **Expected Behavior:** All orders pending for Phase 2 must convert to active holdings immediately upon phase entry.
- **Root Cause:** Boolean condition in `process_deliveries` evaluated phase progression and tick threshold conjunctively rather than checking phase advancement independently.
- **Fix Applied:** Refactored delivery evaluation logic:
  ```python
  should_deliver = (current_phase > item.deliver_in_phase) or (
      current_phase == item.deliver_in_phase and (current_tick >= item.delivery_tick or item.deliver_in_phase > 1)
  )
  ```
- **Verification:** Automated unit tests `test_settlement_delay_holding_pen` and `test_t11_settlement_workflow_holding_pen` pass successfully.

---

### BUG-02: Uninitialized `margin_multiplier` Attribute on Engine Initialization
- **Bug ID:** `BUG-02`
- **Severity:** `CRITICAL` (Runtime crash on order execution)
- **Component:** Core Engine (`src/simulation_engine.py`, `MarginEngine.__init__`)
- **Reported By:** QA Lead | **Fixed By:** Core Dev | **Verified By:** Team Tester
- **What Happened:** Executing an order with `use_margin=True` raised `AttributeError: 'MarginEngine' object has no attribute 'margin_multiplier'`.
- **Steps to Reproduce:**
  1. Initialize `MarginEngine(initial_capital=10000.0)`.
  2. Call `engine.execute_order("VINTRUMITE", "BUY", 400.0, 100.0, use_margin=True)`.
- **Actual Behavior:** Python runtime crash with `AttributeError`.
- **Expected Behavior:** Engine calculates required cash using margin multiplier derived from `initial_margin_ratio` without raising exceptions.
- **Root Cause:** Attribute `self.margin_multiplier` was only set inside `set_margin_multiplier()`, but never initialized in `MarginEngine.__init__`.
- **Fix Applied:** Added explicit initialization in `MarginEngine.__init__`:
  ```python
  self.margin_multiplier = 1.0 / initial_margin_ratio
  ```
  And ensured `set_margin_multiplier` maintains synchronization:
  ```python
  self.margin_multiplier = multiplier
  ```
- **Verification:** Verified via `test_t02_normal_leveraged_trading` and full regression suite.

---

### BUG-03: Floating-Point Division Display Precision Artifacts
- **Bug ID:** `BUG-03`
- **Severity:** `MAJOR` (Degrades financial readability)
- **Component:** Financial HUD (`index.html` & `src/simulation_engine.py`)
- **Reported By:** Team Tester | **Fixed By:** Frontend Dev | **Verified By:** Financial Analyst
- **What Happened:** Displayed margin ratio and equity metrics rendered unrounded raw IEEE 754 floating point values such as `24.9999999994%` or `Equity: $7499.9999999`.
- **Steps to Reproduce:**
  1. Place leveraged order of $40,000 with $10,000 cash.
  2. Advance price ticks down by 6.25%.
  3. Inspect HUD gauge.
- **Actual Behavior:** Numbers displayed up to 12 decimal places, overflowing the visual gauge.
- **Expected Behavior:** All monetary values formatted to 2 decimal places with currency symbol; percentages formatted as `XX.XX%`.
- **Root Cause:** Missing explicit formatting filter on raw numeric string interpolations.
- **Fix Applied:** Added `.toFixed(2)` in JavaScript UI formatters and `round(val, 2)` in backend API serializations.
- **Verification:** UI visual inspection confirms clean formatting: `20.00%` and `$7,500.00`.

---

### BUG-04: Missing Pre-Trade Guard on Liquidated Account State
- **Bug ID:** `BUG-04`
- **Severity:** `MAJOR` (Financial rule violation)
- **Component:** Core Engine (`src/simulation_engine.py`, `execute_order`)
- **Reported By:** QA Lead | **Fixed By:** Core Dev | **Verified By:** Financial Analyst
- **What Happened:** After an account was liquidated (`is_liquidated = True`), a player could still submit new order requests, potentially mutating remaining cash balances.
- **Steps to Reproduce:**
  1. Trigger forced liquidation via `trigger_forced_liquidation()`.
  2. Call `execute_order("VINTRUMITE", "BUY", 10.0, 50.0)`.
- **Actual Behavior:** Order executed against remaining post-liquidation cash.
- **Expected Behavior:** Broker trading privileges must be permanently locked upon liquidation.
- **Root Cause:** Missing state guard check at the entry point of `execute_order`.
- **Fix Applied:** Added strict state guard:
  ```python
  if self.state.is_liquidated:
      raise RuntimeError("Account is liquidated. Trading is disabled.")
  ```
- **Verification:** Unit test `test_t08_post_liquidation_lockout` verifies `RuntimeError` is raised.

---

### BUG-05: Margin Ratio Definition for Pure Cash Positions
- **Bug ID:** `BUG-05`
- **Severity:** `MAJOR` (Financial interpretation ambiguity)
- **Component:** Engine Valuation (`evaluate` method)
- **Reported By:** Financial Analyst | **Fixed By:** Backend Dev | **Verified By:** QA Lead
- **What Happened:** When buying $5,000 stock with $10,000 cash (0 debt), formula $\text{Equity} / \text{Stock Value} = 10,000 / 5,000$ produced $2.0$ ($200\%$). While mathematically correct, users expected unleveraged cash positions to indicate standard solvent status without confusing ratio numbers.
- **Steps to Reproduce:**
  1. Execute cash BUY of $5,000 with initial capital $10,000.
  2. Inspect `metrics.margin_ratio` and `metrics.margin_status`.
- **Actual Behavior:** Ratio was $2.0$, and status was `HEALTHY`.
- **Expected Behavior:** Status must clearly reflect `HEALTHY` (no debt), and test assertions must explicitly document that $\text{Equity} > \text{Stock Value}$ represents an unleveraged cash cushion.
- **Fix Applied:** Updated documentation and test case `test_t01_normal_cash_trading` with explicit financial comments explaining the cash cushion ratio.
- **Verification:** Test passed and documentation verified against core specifications.

---

### BUG-06: Repository Root Clutter from Loose Asset Files
- **Bug ID:** `BUG-06`
- **Severity:** `MINOR` (Repository cleanliness & maintainability)
- **Component:** Repository Structure
- **Reported By:** Team Tester | **Fixed By:** Repo Admin | **Verified By:** QA Lead
- **What Happened:** Background test images (`bg_base.png`, `bg_reveal.png`) remained in the repository root directory after initial testing.
- **Steps to Reproduce:** Check directory listing in project root.
- **Actual Behavior:** Root directory contained orphan PNG files.
- **Expected Behavior:** All media assets organized cleanly inside `assets/` subfolder.
- **Fix Applied:** Moved images into `assets/` and updated all HTML reference paths.
- **Verification:** Repository root directory clean; asset links operational.

---

## 4. Triage Summary & Release Sign-Off

- **Total Defects Logged:** 6
- **Critical Defects Resolved:** 2 / 2 (100%)
- **Major Defects Resolved:** 3 / 3 (100%)
- **Minor Defects Resolved:** 1 / 1 (100%)
- **Open Defects Blocking Core Flow:** 0
- **Regression Testing Status:** PASSED (All automated unit tests pass cleanly)
- **Sign-Off Decision:** Approved for Checkpoint 6 Scope Freeze.
