# FORMAL TEST SPECIFICATION AND VERIFICATION TABLE

**Project:** Free Fall 2.0  
**Phase:** Week 6 — Build, Integration and Testing Clinic  
**Standard:** 4-Tier Test Framework (Normal, Boundary, Invalid, Financial Consistency)  

---

## 1. Testing Framework and Methodology

In accordance with Week 6 Clinic guidelines (*Slides 16–19*), testing verifies that the simulation is **both technically operational and financially explainable**:
- **Technical Pass:** Buttons respond, state transitions occur, order executions mutate account attributes, and no runtime exceptions occur on valid inputs.
- **Financial Consistency:** All accounting identities, leverage bounds, liquidation trigger thresholds, and settlement queues strictly match approved financial formulas.

Every test case records:
$$\text{ID} \quad \vert \quad \text{Category} \quad \vert \quad \text{Input} \quad \vert \quad \text{Expected Result} \quad \vert \quad \text{Actual Result} \quad \vert \quad \text{Status} \quad \vert \quad \text{Author} \quad \vert \quad \text{Fixer} \quad \vert \quad \text{Verifier}$$

---

## 2. Master Test Table (Slide 18)

| ID | Category | Test Case & Inputs | Expected Result | Actual Result | Status | Author | Fixer | Verifier |
|:---:|:---:|---|---|---|:---:|:---:|:---:|:---:|
| **T01** | **Normal** | Capital $10,000, 1.0x Cash, BUY 10 shares @ $500, Price flat ($500). | Cash: $5,000; Margin Debt: $0; Equity: $10,000; Margin Ratio: 200% (Cash Cushion); Status: `HEALTHY`. | Cash: $5,000; Debt: $0; Equity: $10,000; Ratio: 200%; Status: `HEALTHY`. | **PASS** | QA Lead | N/A | Financial Analyst |
| **T02** | **Normal** | Capital $10,000, 4.0x Margin Tier, BUY 400 shares @ $100 (Cost $40,000). | Cash: $0; Margin Debt: $30,000; Equity: $10,000; Leverage: 4.0x; Margin Ratio: 25.0%; Status: `NORMAL`. | Cash: $0; Debt: $30,000; Equity: $10,000; Leverage: 4.0x; Ratio: 25.0%; Status: `NORMAL`. | **PASS** | QA Lead | N/A | Financial Analyst |
| **T03** | **Boundary** | Position $40,000, Debt $30,000. Price drops -6.25% to $93.75. Position = $37,500. | Equity = $7,500; Margin Ratio = 7,500 / 37,500 = **20.00%** (Exactly on maintenance floor); Status: `WARNING`; Liquidation: NOT triggered. | Margin Ratio: 20.00%; Status: `WARNING`; No liquidation triggered. | **PASS** | QA Lead | Core Dev | Financial Analyst |
| **T04** | **Boundary** | Position $40,000, Debt $30,000. Price drops -6.26% to $93.74. Position = $37,496. | Equity = $7,496; Margin Ratio = 7,496 / 37,496 = **19.9914%** (< 20.00%); Status: `MARGIN_CALL`; Liquidation: Triggered immediately. | Margin Ratio: 19.99%; Status: `MARGIN_CALL`; `trigger_forced_liquidation()` executed. | **PASS** | QA Lead | Core Dev | Financial Analyst |
| **T05** | **Invalid** | Capital $10,000, 4x Tier (Max Power $40,000). Submit BUY order for 500 shares @ $100 ($50,000). | Order rejected with `False`. Cash ($10,000) and Debt ($0) remain untouched. No `NaN` or crash. | Order returns `False`; state unmodified. | **PASS** | QA Lead | Backend Dev | Team Tester |
| **T06** | **Invalid** | Player owns 0 shares of Samsung. Submit SELL order for 50 shares. | Order rejected with `False`. Cash remains untouched. Short selling blocked. | Order returns `False`; state unmodified. | **PASS** | QA Lead | Backend Dev | Team Tester |
| **T07** | **Invalid** | Submit BUY order with negative quantity: `shares = -10.0`. | Engine raises `ValueError("Shares and price must be positive.")`. | `ValueError` raised and caught cleanly before state change. | **PASS** | QA Lead | Backend Dev | Team Tester |
| **T08** | **Invalid** | Account is liquidated. Submit subsequent BUY order. | Engine raises `RuntimeError("Account is liquidated. Trading is disabled.")`. | `RuntimeError` raised and caught cleanly. | **PASS** | QA Lead | Core Dev | Team Tester |
| **T09** | **Financial** | **Week 4 Spec High Leverage Wipeout:** Capital 10m KRW, Debt 30m KRW, -20% Market Shock, 5% Penalty. | Gross Proceeds: 32m; Penalty: 1.6m; Net Proceeds: 30.4m; Debt Repaid: 30m; Final Equity: 400k KRW. | Exact numeric match to approved hand calculation model. | **PASS** | Financial Analyst | Core Dev | QA Lead |
| **T10** | **Financial** | **Safe Haven Bank Protection:** Deposit $4,000 to Bank Savings. Stock market plunges -50%. | Savings balance remains $4,000 (100% nominal value preserved; immune to stock collapse). | Bank Savings = $4,000; Total Portfolio includes full savings balance. | **PASS** | Financial Analyst | Core Dev | QA Lead |
| **T11** | **Workflow** | **T+0.5 Settlement Delay:** Order at Tick 50 (T < 150) vs Order at Tick 200 (T >= 150). | Tick 50 delivered in Phase 1 at Tick 80 (T+30); Tick 200 queued for Phase 2 entry. | Deliveries queued and converted at exact scheduled milestones. | **PASS** | Financial Analyst | Backend Dev | QA Lead |
| **T12** | **Workflow** | **Bank Emergency Reserve Lock:** Savings $5,000, Reserve set to $2,000. Attempt withdraw $4,000. | Withdrawal rejected (Only $3,000 free above reserve). State unchanged. | Returns `False`; Bank balance remains $5,000. | **PASS** | Financial Analyst | Backend Dev | QA Lead |

---

## 3. Detailed Case Specifications & Financial Derivations

### T01 & T02: Normal Trading and Leverage Bounds
- **Formulas Applied:**
  $$\text{Required Cash} = \frac{\text{Order Cost}}{\text{Margin Multiplier}}, \quad \text{New Debt} = \text{Order Cost} - \text{Required Cash}$$
  $$\text{Margin Ratio} = \frac{\text{Equity}}{\text{Stock Portfolio Value}}$$
- **T01 Verification:** Order Cost = $10 \times 500 = \$5,000$. Without margin, Cash decreases by $5,000 (remaining: $5,000). Total Equity = Cash ($5,000) + Stock ($5,000) = $10,000. Margin Ratio = $10,000 / $5,000 = 2.0 (200%), indicating solvent cash cushion.
- **T02 Verification:** Order Cost = $400 \times 100 = \$40,000$. At 4.0x leverage, Required Cash = $40,000 / 4 = $10,000 (Cash becomes $0). New Debt = $30,000. Stock Value = $40,000. Equity = $40,000 - $30,000 = $10,000. Margin Ratio = $10,000 / $40,000 = 25.0% (Initial Margin Ratio satisfied).

---

### T03 & T04: Maintenance Margin Boundary Analysis (Slide 17)
- **Maintenance Threshold:** $20.00\%$ ($\text{Maintenance Margin Ratio} = 0.20$).
- **Boundary Derivation:**
  $$\text{Margin Ratio} = \frac{\text{Stock Value} - \text{Debt}}{\text{Stock Value}} = 1 - \frac{\text{Debt}}{\text{Stock Value}}$$
  $$0.20 = 1 - \frac{30,000}{\text{Stock Value}} \implies \text{Stock Value} = \frac{30,000}{0.80} = \$37,500$$
  $$\text{Share Price} = \frac{\$37,500}{400} = \$93.75 \quad (\text{Drop of } -6.25\%)$$
- **T03 Result (At exactly $93.75):**
  $$\text{Equity} = \$37,500 - \$30,000 = \$7,500, \quad \text{Margin Ratio} = \frac{\$7,500}{\$37,500} = 20.00\%$$
  Status is `WARNING`. No liquidation is triggered.
- **T04 Result (At $93.74, a 1 cent further decline):**
  $$\text{Stock Value} = 400 \times 93.74 = \$37,496.00$$
  $$\text{Equity} = \$37,496.00 - \$30,000 = \$7,496.00, \quad \text{Margin Ratio} = \frac{\$7,496}{\$37,496} = 19.9914\% < 20.00\%$$
  Status switches to `MARGIN_CALL` and triggers `trigger_forced_liquidation()`.

---

### T09: Financial Consistency Check with Approved Specification (Slide 19)
- **Reference Document:** Approved Week 4 Core Financial Logic Specification.
- **Inputs:**
  - Initial Capital: $10,000,000$ KRW
  - Initial Margin Tier: 4.0x (Initial Margin Ratio = 25%)
  - Initial Position: $40,000,000$ KRW ($400$ shares @ $100,000$ KRW)
  - Margin Debt: $30,000,000$ KRW
  - Market Shock: $-20\%$ (Price falls to $80,000$ KRW)
- **Theoretical Financial Settlement:**
  $$\text{Gross Stock Value} = 400 \times 80,000 = 32,000,000 \text{ KRW}$$
  $$\text{Equity} = 32,000,000 - 30,000,000 = 2,000,000 \text{ KRW}$$
  $$\text{Margin Ratio} = \frac{2,000,000}{32,000,000} = 6.25\% < 20.00\% \implies \text{Margin Call}$$
  $$\text{Liquidation Penalty Fee (5\%)} = 32,000,000 \times 0.05 = 1,600,000 \text{ KRW}$$
  $$\text{Net Liquidation Proceeds} = 32,000,000 - 1,600,000 = 30,400,000 \text{ KRW}$$
  $$\text{Margin Debt Repaid} = 30,000,000 \text{ KRW}$$
  $$\text{Final Cash / Equity Remaining} = 30,400,000 - 30,000,000 = 400,000 \text{ KRW}$$
- **Actual Engine Output:**
  ```python
  liq = engine.trigger_forced_liquidation(current_prices)
  # liq['gross_proceeds'] == 32_000_000.0
  # liq['penalty']        == 1_600_000.0
  # liq['net_proceeds']   == 30_400_000.0
  # liq['remaining_debt'] == 0.0
  # liq['final_cash']     == 400_000.0
  ```
- **Conclusion:** Perfect zero-variance match to hand calculation.

---

## 4. Automated Test Runner Evidence

The test suite is executable via standard Python unittest:
```bash
python -m unittest discover tests
```

**Console Execution Output:**
```text
..............
----------------------------------------------------------------------
Ran 14 tests in 0.001s

OK
```

All 14 unit test assertions pass with zero failures and zero errors, certifying the codebase as demo-ready and mathematically sound.
