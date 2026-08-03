"""
aveniva_stress_test.py
======================
Resilience stress-test harness for the Component-1 / C7 token distribution model.

WHAT "RESILIENT" MEANS HERE
---------------------------
A parameter snapshot is resilient if, across a range of volume/activity spike
scenarios, the Reserve Cushion (RC) is never fully drained AND the token
liability the treasury is actually obligated to pay each month stays covered.

THE KEY STRUCTURAL POINT THIS HARNESS TESTS
-------------------------------------------
In run_simulation(), monthly `actual_spend` is bounded by the soft cap (MMS).
But the *effective per-scan rate* is  max(hc_rate * scaling, floor)  — the floor
is applied AFTER the soft-cap scaling. So the treasury's TRUE monthly scan
liability during a spike is:

    floor_liability = floor_new_scan * new_scans + (floor_new_scan / D) * dup_scans

which scales LINEARLY with volume and has NO cap. When floor_liability exceeds
MVT, the gap must come from RC, and nothing in the model bounds it. That is the
real spike exposure, and the model's own `Actual Spend (M)` column under-reports
it whenever the floor binds.

This harness therefore tracks BOTH:
  - the model's RC path (as run_simulation computes it), and
  - a "true-liability" RC path that honours the uncapped floor,
and reports where they diverge.

HOW TO RUN
----------
    pip install pandas numpy
    python aveniva_stress_test.py

Outputs: a console summary + CSV files written next to this script:
    stress_scenario_sweep.csv     — every deterministic spike scenario
    stress_montecarlo.csv         — Monte Carlo draws
    stress_robust_ranking.csv     — parameter snapshots ranked by worst-case margin
    stress_sensitivity.csv        — tornado sensitivity on worst-case min-RC

Author: resilience harness for Spoon (fka Aveniva) internal use, July 2026.
"""

from __future__ import annotations

import os
import sys
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Import the ENGINE from the dashboard so this harness stays a faithful mirror
# of production logic (single source of truth). main() is guarded by
# `if __name__ == "__main__"`, so importing the module only runs the cheap
# module-level setup, not the Streamlit app.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from aveniva_incentivization_dashboard_v1 import (
        TokenomicsParams,
        derive_metrics,
        run_simulation,
    )
except Exception as exc:  # pragma: no cover
    print("Could not import the dashboard engine:", exc)
    print("Make sure aveniva_incentivization_dashboard_v1.py is in the same folder "
          "and that streamlit/plotly are installed (they are imported at module load).")
    raise

HERE = os.path.dirname(os.path.abspath(__file__))
N_MONTHS = 36


# ==============================================================================
# 0. DEFAULT SNAPSHOT  (mirrors the sidebar defaults in the dashboard)
# ==============================================================================

DEFAULT_PARAMS = TokenomicsParams(
    total_supply_m=2560.0,
    community_pool_pct=39.0,
    tge_unlock_pct=20.0,
    vesting_months=60,
    mms_multiplier=2.0,
    scan_pct=80.0,
    raffle_pct=7.0,
    contributor_pct=7.0,
    quests_pct=6.0,
    new_scans_monthly=10_000,
    dup_scans_monthly=100_000,
    dup_divisor=20.0,
    token_price_eur=0.0098,
    floor_new_scan=70,
    testnet_alpha=5.0,
    testnet_pool_m=128.0,
    testnet_months=6,
    hard_cap_new_scan=250,
    spike_multiplier=5.0,
    spike_months=2,
    decay_months=4,
    testnet_chart_users=1_000,
    # C2 tier multipliers ON by default — the flat-rate assumption understated
    # scan cost (supervisor feedback, C2 point 2).
    enable_tier_multipliers=True,
)


# ==============================================================================
# 1. DIAGNOSTICS LAYER  — wraps run_simulation and adds the true-floor accounting
# ==============================================================================

def evaluate(p: TokenomicsParams,
             n_months: int = N_MONTHS,
             rc_buffer_frac: float = 0.0) -> dict:
    """
    Run the model for one parameter snapshot and compute resilience diagnostics.

    Adds, on top of run_simulation():
      - true_floor_liability (M) per month: the uncapped floor obligation.
      - a "true-liability" RC path that draws RC to honour that floor when it
        exceeds what the model books as actual_spend.
      - summary resilience metrics.

    `rc_buffer_frac` sets a safety floor on RC: min acceptable RC = frac * initial RC.
    """
    d = derive_metrics(p)
    sim = run_simulation(p, d, n_months=n_months)

    mvt_m = d.mvt_m
    rc0 = d.rc_m
    D = max(p.dup_divisor, 1.0)

    # --- True (uncapped) floor liability per month ---
    # Reference quantity: what the floor guarantee alone obliges, ignoring rates.
    floor_liab_m = (
        p.floor_new_scan * sim["New Scans"]
        + (p.floor_new_scan / D) * sim["Dup Scans"]
    ) / 1e6

    # Non-scan fixed budgets (these also get paid; they scale with soft cap in the
    # model, but the treasury still owes raffle/contributor/quests at MVT rate).
    non_scan_m = (d.raffle_budget_m + d.contributor_budget_m + d.quests_budget_m)

    # --- TRUE monthly scan payout ---
    # Uses the simulation's own effective per-scan rates, which are
    # max(blended_rate × soft_cap_scaling, floor) — so this captures BOTH the
    # uncapped floor guarantee AND the C2 tier multipliers. Deriving this from
    # the floor alone (as an earlier version did) understated the obligation
    # whenever the paid rate sat above the floor.
    true_scan_m = (
        sim["New Scans"] * sim["Eff New Scan Tokens"]
        + sim["Dup Scans"] * sim["Eff Dup Tokens"]
    ) / 1e6

    # The TRUE monthly obligation = actual scan payout + non-scan budgets.
    true_obligation_m = true_scan_m + non_scan_m

    # The model's booked spend (soft-cap bounded).
    model_spend_m = sim["Actual Spend (M)"]

    # Where the floor binds beyond what the model books:
    under_report_m = (true_obligation_m - model_spend_m).clip(lower=0)

    # --- Re-derive a corrected RC path honouring the TRUE obligation ---
    # We reuse the model's hard-cap-savings inflow (it still applies), but replace
    # the drawdown with the true obligation.
    hc_savings_in = sim["HC Savings to RC (M)"]
    rc = rc0
    rc_path_true = []
    for i in range(len(sim)):
        drawdown = max(0.0, true_obligation_m.iloc[i] - mvt_m)
        rollover = max(0.0, mvt_m - true_obligation_m.iloc[i])
        rc = rc - drawdown + rollover + hc_savings_in.iloc[i]
        rc = max(0.0, min(rc, rc0))
        rc_path_true.append(rc)
    rc_path_true = pd.Series(rc_path_true, index=sim.index)

    # Did the TRUE obligation ever exceed available funding (MVT + RC on hand)?
    # Reconstruct RC-on-hand *before* each month's draw for the true path.
    rc_before = [rc0]
    for v in rc_path_true.tolist()[:-1]:
        rc_before.append(v)
    rc_before = pd.Series(rc_before, index=sim.index)
    funding_available = mvt_m + rc_before
    breach = true_obligation_m > funding_available  # treasury cannot pay in full

    rc_min_buffer = rc_buffer_frac * rc0

    diag = {
        "min_rc_model_m": float(sim["RC Balance (M)"].min()),
        "min_rc_true_m": float(rc_path_true.min()),
        "rc0_m": float(rc0),
        "mvt_m": float(mvt_m),
        "mms_m": float(d.mms_m),
        "base_new_scan_tokens": float(d.base_new_scan_tokens),
        "eff_new_scan_tokens_ss": float(d.effective_new_scan_tokens),
        "soft_cap_months": int(sim["Soft Cap Fired"].sum()),
        "max_true_obligation_m": float(true_obligation_m.max()),
        "max_under_report_m": float(under_report_m.max()),
        "n_breach_months": int(breach.sum()),
        "rc_depletes_model": bool(sim["RC Balance (M)"].min() <= rc_min_buffer + 1e-9),
        "rc_depletes_true": bool(rc_path_true.min() <= rc_min_buffer + 1e-9),
        # worst-case margin: how much RC headroom remains at the worst month
        # under the TRUE obligation (negative => it would have gone under).
        "worst_margin_true_m": float(rc_path_true.min() - rc_min_buffer),
    }

    detail = sim.copy()
    detail["True Floor Liab (M)"] = floor_liab_m.round(3)
    detail["True Scan Payout (M)"] = true_scan_m.round(3)
    detail["True Obligation (M)"] = true_obligation_m.round(3)
    detail["Under-report vs model (M)"] = under_report_m.round(3)
    detail["RC Balance TRUE (M)"] = rc_path_true.round(3)
    detail["Breach (can't fully pay)"] = breach

    return {"diag": diag, "detail": detail, "metrics": d}


# ==============================================================================
# 2. CLOSED-FORM PRE-FILTER  — cheap fragility screens (no simulation needed)
# ==============================================================================

def closed_form_guardrails(p: TokenomicsParams) -> dict:
    """
    Fast algebraic checks that flag obviously-fragile snapshots before simulating.
    """
    d = derive_metrics(p)
    D = max(p.dup_divisor, 1.0)

    # Max soft-cap-bounded monthly drawdown (pool-driven spend only).
    max_pool_drawdown_m = d.mms_m - d.mvt_m  # = MVT*(mms_multiplier-1)
    months_to_depletion_pool = (
        d.rc_m / max_pool_drawdown_m if max_pool_drawdown_m > 0 else float("inf")
    )

    # Volume at which the UNCAPPED floor obligation exactly equals MVT
    # (beyond this, every extra scan draws RC no matter what the soft cap says).
    # floor * new + (floor/D)*dup = MVT_tokens, with dup = new * (dup/new ratio).
    ratio = p.dup_scans_monthly / max(p.new_scans_monthly, 1)
    per_new_floor_cost = p.floor_new_scan + (p.floor_new_scan / D) * ratio
    mvt_tokens = d.mvt_m * 1e6
    new_scans_floor_breakeven = (
        mvt_tokens / per_new_floor_cost if per_new_floor_cost > 0 else float("inf")
    )
    floor_breakeven_mult = new_scans_floor_breakeven / max(p.new_scans_monthly, 1)

    return {
        "max_pool_drawdown_m": round(max_pool_drawdown_m, 3),
        "months_to_depletion_pool": round(months_to_depletion_pool, 1),
        "floor_breakeven_new_scans": int(new_scans_floor_breakeven),
        "floor_breakeven_vol_mult": round(floor_breakeven_mult, 2),
        "hard_cap_binds": bool(d.base_new_scan_tokens > p.hard_cap_new_scan),
    }


# ==============================================================================
# 3. SCENARIO SWEEP  — deterministic adversarial spikes
# ==============================================================================

def make_scenario(base: TokenomicsParams,
                  spike_multiplier: float,
                  spike_months: int,
                  decay_months: int,
                  sustained_vol_mult: float) -> TokenomicsParams:
    """
    Build a param set for one stress scenario.

    - spike_* / decay_months drive the TRANSIENT launch curve in run_simulation.
    - sustained_vol_mult raises the STEADY-STATE base volume (re-derives rates,
      because the pool rate is back-calculated from steady-state volume).
    """
    return replace(
        base,
        spike_multiplier=spike_multiplier,
        spike_months=spike_months,
        decay_months=decay_months,
        new_scans_monthly=int(base.new_scans_monthly * sustained_vol_mult),
        dup_scans_monthly=int(base.dup_scans_monthly * sustained_vol_mult),
    )


def scenario_sweep(base: TokenomicsParams,
                   spike_mults=(2, 5, 10, 15, 20),
                   spike_lens=(1, 3, 6),
                   decay_lens=(2, 6),
                   sustained=(1.0, 2.0, 5.0),
                   rc_buffer_frac: float = 0.0) -> pd.DataFrame:
    """
    Cartesian product of adversarial spike scenarios. Records min-RC (model &
    true) and floor-breach counts for each.
    """
    rows = []
    for sm, sl, dl, sus in itertools.product(spike_mults, spike_lens, decay_lens, sustained):
        p = make_scenario(base, sm, sl, dl, sus)
        res = evaluate(p, rc_buffer_frac=rc_buffer_frac)
        dg = res["diag"]
        rows.append({
            "spike_mult": sm,
            "spike_months": sl,
            "decay_months": dl,
            "sustained_vol_mult": sus,
            "min_rc_model_m": round(dg["min_rc_model_m"], 3),
            "min_rc_true_m": round(dg["min_rc_true_m"], 3),
            "soft_cap_months": dg["soft_cap_months"],
            "breach_months": dg["n_breach_months"],
            "max_under_report_m": round(dg["max_under_report_m"], 3),
            "rc_depletes_true": dg["rc_depletes_true"],
            "PASS": (not dg["rc_depletes_true"]) and dg["n_breach_months"] == 0,
        })
    return pd.DataFrame(rows)


# ==============================================================================
# 4. MONTE CARLO  — chance-constraint pass rate
# ==============================================================================

def monte_carlo(base: TokenomicsParams,
                n_draws: int = 500,
                seed: int = 7,
                rc_buffer_frac: float = 0.0) -> pd.DataFrame:
    """
    Sample plausible spike scenarios and compute the fraction that keep RC solvent
    AND fully cover the true obligation. A resilient snapshot passes in >=95-99%.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n_draws):
        sm = float(rng.uniform(1.5, 20.0))          # spike magnitude
        sl = int(rng.integers(1, 7))                # spike length (1-6 mo)
        dl = int(rng.integers(1, 13))               # decay length (1-12 mo)
        sus = float(rng.lognormal(mean=0.3, sigma=0.5))  # sustained growth factor
        sus = float(np.clip(sus, 0.7, 8.0))
        p = make_scenario(base, sm, sl, dl, sus)
        dg = evaluate(p, rc_buffer_frac=rc_buffer_frac)["diag"]
        rows.append({
            "spike_mult": round(sm, 2),
            "spike_months": sl,
            "decay_months": dl,
            "sustained_vol_mult": round(sus, 2),
            "min_rc_true_m": round(dg["min_rc_true_m"], 3),
            "breach_months": dg["n_breach_months"],
            "PASS": (not dg["rc_depletes_true"]) and dg["n_breach_months"] == 0,
        })
    return pd.DataFrame(rows)


# ==============================================================================
# 5. ROBUST SEARCH  — max-min over free params against the scenario set
# ==============================================================================

def robust_search(base: TokenomicsParams,
                  grid: dict | None = None,
                  scenario_kwargs: dict | None = None,
                  rc_buffer_frac: float = 0.10,
                  top_n: int = 15) -> pd.DataFrame:
    """
    Search free parameters for the snapshot with the best WORST-CASE resilience
    across the scenario sweep (robust / max-min objective).

    Objective per candidate = min over all scenarios of the TRUE worst-margin
    (RC headroom above the safety buffer at the worst month). Higher is better.
    We also record the mean margin and pass rate as tie-breakers.
    """
    if grid is None:
        grid = {
            "tge_unlock_pct": [15, 20, 25, 30],
            "vesting_months": [36, 48, 60],
            "mms_multiplier": [1.5, 2.0, 3.0],
            "hard_cap_new_scan": [150, 250, 400],
            "floor_new_scan": [20, 40, 70],
        }
    if scenario_kwargs is None:
        # Slightly leaner scenario set to keep the search tractable.
        scenario_kwargs = dict(
            spike_mults=(5, 10, 20),
            spike_lens=(1, 3),
            decay_lens=(6,),
            sustained=(1.0, 2.0, 5.0),
        )

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    rows = []
    for combo in combos:
        overrides = dict(zip(keys, combo))
        # Enforce hard_cap >= floor (model constraint).
        if overrides["hard_cap_new_scan"] < overrides["floor_new_scan"]:
            continue
        cand = replace(base, **overrides)
        sweep = scenario_sweep(cand, rc_buffer_frac=rc_buffer_frac, **scenario_kwargs)
        # true worst-margin per scenario = min_rc_true - buffer; recompute buffer:
        rc0 = derive_metrics(cand).rc_m
        margins = sweep["min_rc_true_m"] - rc_buffer_frac * rc0
        rows.append({
            **overrides,
            "worst_margin_m": round(float(margins.min()), 3),
            "mean_margin_m": round(float(margins.mean()), 3),
            "pass_rate": round(float(sweep["PASS"].mean()), 3),
            "rc0_m": round(rc0, 2),
        })
    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["worst_margin_m", "pass_rate", "mean_margin_m"], ascending=False
    ).reset_index(drop=True)
    return df.head(top_n)


# ==============================================================================
# 6. SENSITIVITY  — one-at-a-time tornado on worst-case resilience
# ==============================================================================

def sensitivity_tornado(base: TokenomicsParams,
                         perturb: dict | None = None,
                         scenario_kwargs: dict | None = None,
                         rc_buffer_frac: float = 0.10) -> pd.DataFrame:
    """
    Vary each parameter low/high (one at a time) and measure the swing in the
    worst-case TRUE margin across the scenario sweep. Ranks which knobs matter.
    """
    if perturb is None:
        perturb = {
            "tge_unlock_pct": (15, 30),
            "vesting_months": (36, 72),
            "mms_multiplier": (1.5, 3.0),
            "hard_cap_new_scan": (150, 400),
            "floor_new_scan": (20, 100),
            "scan_pct": (70, 88),
        }
    if scenario_kwargs is None:
        scenario_kwargs = dict(
            spike_mults=(5, 10, 20),
            spike_lens=(1, 3),
            decay_lens=(6,),
            sustained=(1.0, 2.0, 5.0),
        )

    def worst_margin(pp: TokenomicsParams) -> float:
        sweep = scenario_sweep(pp, rc_buffer_frac=rc_buffer_frac, **scenario_kwargs)
        rc0 = derive_metrics(pp).rc_m
        return float((sweep["min_rc_true_m"] - rc_buffer_frac * rc0).min())

    baseline = worst_margin(base)
    rows = []
    for k, (lo, hi) in perturb.items():
        # scan_pct change must keep splits valid: shrink/grow quests as remainder.
        def with_val(val):
            if k == "scan_pct":
                rem = 100 - val
                # keep raffle/contributor proportional-ish, quests = remainder
                raf = min(base.raffle_pct, max(1, rem - 2))
                con = min(base.contributor_pct, max(1, rem - raf - 1))
                q = rem - raf - con
                return replace(base, scan_pct=val, raffle_pct=raf,
                               contributor_pct=con, quests_pct=q)
            return replace(base, **{k: val})

        m_lo = worst_margin(with_val(lo))
        m_hi = worst_margin(with_val(hi))
        rows.append({
            "param": k,
            "low_val": lo,
            "high_val": hi,
            "margin_low_m": round(m_lo, 3),
            "margin_high_m": round(m_hi, 3),
            "swing_m": round(abs(m_hi - m_lo), 3),
        })
    df = pd.DataFrame(rows).sort_values("swing_m", ascending=False).reset_index(drop=True)
    df.attrs["baseline_worst_margin_m"] = round(baseline, 3)
    return df


# ==============================================================================
# 7. MAIN
# ==============================================================================

def main():
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 40)

    print("=" * 78)
    print("SPOON (fka Aveniva) — Token Incentivization Resilience Stress Test")
    print("=" * 78)

    # --- 0. Baseline snapshot diagnostics ---
    base = DEFAULT_PARAMS
    gr = closed_form_guardrails(base)
    print("\n[0] CLOSED-FORM GUARDRAILS (current default snapshot)")
    print(f"    Max soft-cap-bounded monthly RC drawdown : {gr['max_pool_drawdown_m']:.3f} M $AVA")
    print(f"    Months to RC depletion (pool spend only)  : {gr['months_to_depletion_pool']}")
    print(f"    Floor breaks even with MVT at volume mult : {gr['floor_breakeven_vol_mult']}x "
          f"(~{gr['floor_breakeven_new_scans']:,} new scans/mo)")
    print(f"    Hard cap currently binds the pool rate?   : {gr['hard_cap_binds']}")

    base_eval = evaluate(base)
    dg = base_eval["diag"]
    print("\n[1] BASELINE 36-MONTH RUN (default launch curve)")
    print(f"    Initial RC                       : {dg['rc0_m']:.2f} M   MVT: {dg['mvt_m']:.3f} M/mo   MMS: {dg['mms_m']:.3f} M/mo")
    print(f"    Min RC (model path)              : {dg['min_rc_model_m']:.3f} M")
    print(f"    Min RC (true-floor path)         : {dg['min_rc_true_m']:.3f} M")
    print(f"    Soft-cap months                  : {dg['soft_cap_months']}")
    print(f"    Max under-reported liability      : {dg['max_under_report_m']:.3f} M/mo "
          f"(model books less than the treasury actually owes)")
    print(f"    Months treasury cannot fully pay  : {dg['n_breach_months']}")

    # --- 2. Deterministic scenario sweep ---
    print("\n[2] SCENARIO SWEEP (deterministic adversarial spikes)")
    sweep = scenario_sweep(base)
    n_fail = int((~sweep["PASS"]).sum())
    print(f"    {len(sweep)} scenarios | PASS: {int(sweep['PASS'].sum())} | FAIL: {n_fail}")
    worst = sweep.sort_values("min_rc_true_m").head(8)
    print("    Worst 8 scenarios by true min-RC:")
    print(worst.to_string(index=False))
    sweep.to_csv(os.path.join(HERE, "stress_scenario_sweep.csv"), index=False)

    # --- 3. Monte Carlo ---
    print("\n[3] MONTE CARLO (chance constraint)")
    mc = monte_carlo(base, n_draws=500)
    pass_rate = float(mc["PASS"].mean())
    print(f"    500 draws | pass rate (RC solvent & fully covered): {pass_rate*100:.1f}%")
    print(f"    5th-pctile true min-RC: {mc['min_rc_true_m'].quantile(0.05):.3f} M")
    mc.to_csv(os.path.join(HERE, "stress_montecarlo.csv"), index=False)

    # --- 4. Sensitivity ---
    print("\n[4] SENSITIVITY (tornado on worst-case true margin)")
    tor = sensitivity_tornado(base)
    print(f"    Baseline worst-case margin: {tor.attrs['baseline_worst_margin_m']:.3f} M")
    print(tor.to_string(index=False))
    tor.to_csv(os.path.join(HERE, "stress_sensitivity.csv"), index=False)

    # --- 5. Robust search ---
    print("\n[5] ROBUST SEARCH (max-min over free params, 10% RC safety buffer)")
    rob = robust_search(base, rc_buffer_frac=0.10)
    print("    Top resilient snapshots (worst_margin_m higher = more resilient):")
    print(rob.to_string(index=False))
    rob.to_csv(os.path.join(HERE, "stress_robust_ranking.csv"), index=False)

    print("\n" + "=" * 78)
    print("CSV outputs written next to this script:")
    for f in ("stress_scenario_sweep.csv", "stress_montecarlo.csv",
              "stress_sensitivity.csv", "stress_robust_ranking.csv"):
        print("   ", f)
    print("=" * 78)


if __name__ == "__main__":
    main()
