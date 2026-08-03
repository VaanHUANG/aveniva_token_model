"""
aveniva_tokenomics_dashboard.py
================================
Aveniva — Component 1 Token Distribution Model
Interactive Streamlit dashboard for tuning and stress-testing the
token incentivization system. All charts update in real time as
hyperparameters are adjusted in the sidebar.

HOW TO RUN LOCALLY
------------------
    pip install streamlit plotly pandas numpy
    streamlit run aveniva_tokenomics_dashboard.py

HOW TO SHARE WITH INVESTORS (free, no login required for viewers)
------------------------------------------------------------------
    1. Push this file to a public GitHub repository
    2. Go to https://share.streamlit.io
    3. Connect your GitHub repo → select this file → Deploy
    4. Share the generated URL

ARCHITECTURE OF THIS FILE
--------------------------
  1. CONFIGURATION      — page setup, colour palette
  2. DATA CLASSES       — TokenomicsParams (inputs) and DerivedMetrics (outputs)
  3. CALCULATION ENGINE — derive_metrics() and run_simulation()
  4. CHART FUNCTIONS    — one function per visualisation
  5. SIDEBAR            — render_sidebar() → TokenomicsParams
  6. MAIN APP           — tab layout, metric cards, chart rendering

MODIFYING THIS FILE
-------------------
  • To add a new reward category: add a field to TokenomicsParams, derive its
    budget in derive_metrics(), and add a row to build_rate_table().
  • To change the simulation volume model: edit the volume multiplier block
    inside run_simulation().
  • To add a new chart: write a fig_*() function and call it in main().

Author: generated for Aveniva internal use, May 2026.
"""

# ==============================================================================
# 0. IMPORTS
# ==============================================================================

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from dataclasses import dataclass


# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

st.set_page_config(
    page_title="Aveniva — Token Distribution Model",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Colour palette — edit here to retheme the entire dashboard
COLORS = {
    "scan":        "#2E86AB",   # Scan-to-Earn — blue
    "raffle":      "#A23B72",   # Raffle — purple
    "contributor": "#F18F01",   # Contributor Tracks — amber
    "quests":      "#3BB273",   # Quests & Campaigns — green
    "mvt":         "#44BBA4",   # Monthly Vesting Tranche — teal
    "mms":         "#F5A623",   # Maximum Monthly Spend — yellow
    "floor":       "#E94F37",   # Floor guarantee — red
    "rc_line":     "#A8DADC",   # RC balance line — light blue
    "cap_marker":  "#FFD166",   # Soft-cap-fired marker — gold
    "neutral":     "#8D99AE",   # Neutral / secondary — grey-blue
}

# Used for the bucket pie/donut chart (must match category order)
BUCKET_COLORS = [
    COLORS["scan"],
    COLORS["raffle"],
    COLORS["contributor"],
    COLORS["quests"],
]


# ==============================================================================
# 2. DATA CLASSES
# ==============================================================================

# ---- Component 2 tier structure (mirrors aveniva_progression_dashboard.py) ----
# C2 defines 6 named tiers over overall levels 1–30, each with a scan-reward
# multiplier. C1 previously assumed a flat rate, ignoring these entirely
# (supervisor feedback, C2 point 2). These are LOCKED to the C2 spec — the
# user-tunable parts (tier mix, scan intensity, ramp) live in TokenomicsParams.
C2_TIER_NAMES = ["Scout", "Taster", "Collector", "Analyst", "Expert", "Legend"]
C2_SCAN_MULT  = [1.00,    1.15,     1.30,        1.50,      1.75,     2.00]

# Default steady-state population mix (% of users per tier) — a pyramid, since
# higher tiers require substantial cumulative XP. ASSUMPTION: revisit against
# real cohort data once available.
C2_DEFAULT_MIX = [40.0, 25.0, 18.0, 10.0, 5.0, 2.0]

# Relative scans per user per tier. Cost is VOLUME-weighted, not headcount-
# weighted: high-tier users both earn more per scan AND scan more, so a 2%
# Legend population carries far more than 2% of scan volume. ASSUMPTION.
C2_DEFAULT_INTENSITY = [1.0, 1.5, 2.2, 3.2, 4.5, 6.0]


@dataclass
class TokenomicsParams:
    """
    All user-configurable hyperparameters.
    Populated entirely from sidebar widgets — never hardcoded in logic functions.
    To add a new parameter: add a field here, a widget in render_sidebar(),
    and consumption in derive_metrics() or run_simulation().
    """
    # ---- Supply -----------------------------------------------------------
    total_supply_m: float       # Total token supply in millions (e.g. 2560)
    community_pool_pct: float   # Community pool as % of total supply (e.g. 39)
    tge_unlock_pct: float       # % of community pool unlocked at TGE (e.g. 20)
    vesting_months: int         # Linear vesting duration after TGE (e.g. 60)

    # ---- Safety -----------------------------------------------------------
    mms_multiplier: float       # Max Monthly Spend = MVT × this value (e.g. 2.0)

    # ---- Category splits (must sum to 100) --------------------------------
    scan_pct: float             # % of MVT for Scan-to-Earn
    raffle_pct: float           # % for Raffle prize pool
    contributor_pct: float      # % for Contributor Tracks & Reputation
    quests_pct: float           # % for Quests & Campaigns (auto-derived as remainder)

    # ---- Volume (steady state) --------------------------------------------
    new_scans_monthly: int      # Unique new product scans per month at steady state
    dup_scans_monthly: int      # Duplicate / re-scans per month at steady state
    dup_divisor: float          # Duplicate rate = new_scan_rate / dup_divisor

    # ---- Price & floor ----------------------------------------------------
    token_price_eur: float      # Token price at ICO in EUR (used for EUR displays only)
    floor_new_scan: int         # Hard floor: minimum tokens per new scan

    # ---- Testnet ----------------------------------------------------------
    testnet_alpha: float        # Multiplier α applied to all mainnet base rates
    testnet_pool_m: float       # Testnet token pool in millions
    testnet_months: int         # Number of months the testnet phase runs
    # testnet_tge_pct is declared at the end of this dataclass (default 25.0)
    # because dataclass fields with defaults must follow those without.

    # ---- Hard cap ---------------------------------------------------------
    hard_cap_new_scan: int      # Maximum $AVA per new scan; excess scan budget flows to RC

    # ---- Simulation volume curve ------------------------------------------
    spike_multiplier: float     # Volume multiplier during the launch spike window
    spike_months: int           # How many months the full spike lasts
    decay_months: int           # Months to linearly decay from spike back to 1×

    # ---- RC combined timeline chart ---------------------------------------
    testnet_chart_users: int    # Active testnet users assumed for the RC timeline chart

    # ---- Testnet TGE unlock (allocation sheet: 25% at TGE, rest vests) ----
    testnet_tge_pct: float = 25.0   # % of testnet pool unlocked at TGE; remainder
                                    # vests linearly over testnet_months

    # ---- Duplicate-scan model (supervisor feedback, C1 point 4) -----------
    # "static":   duplicate volume = the fixed sidebar input × vol multiplier
    #             (legacy behaviour; new and dup scale together).
    # "coverage": the new/dup split derives from catalogue coverage (the
    #             supervisor's dup_prob = C_t/N idea, integrated within each
    #             month so that a month's scans also overlap each other —
    #             the coupon-collector form):
    #                 new_t = (N − C_t) · (1 − exp(−S_t / (w·N)))
    #                 dup_t = S_t − new_t;   C_{t+1} = C_t + new_t
    #             where S_t = total scan demand, C_t = cumulative catalogue,
    #             N = item universe, w = popularity weight (w > 1 concentrates
    #             scans on popular items Zipf-style, shrinking effective
    #             discovery breadth; w = 1 is the pure uniform model).
    #             New scans start high (sparse catalogue) and taper as C_t → N,
    #             duplicates start low and rise — fixing the launch-curve shape
    #             flagged in supervisor feedback point 6. The sidebar new/dup
    #             volume inputs define TOTAL scan demand in this mode.
    # ---- Component 2 scan multipliers (supervisor feedback, C2 point 2) ----
    # C1 previously priced every scan flat, ignoring C2's 1.00×–2.00× tier
    # multipliers. When enabled, the per-scan payout is blended across the tier
    # population and the hard cap binds LAST — i.e. on the final paid amount,
    # post-multiplier — so no tier can ever be paid above hard_cap_new_scan.
    enable_tier_multipliers: bool = True
    tier_mix_pct: tuple = tuple(C2_DEFAULT_MIX)            # % of users per tier
    tier_scan_intensity: tuple = tuple(C2_DEFAULT_INTENSITY)  # relative scans/user
    tier_ramp_months: int = 12   # months for the population to migrate from
                                 # all-Scout at launch to tier_mix_pct.
                                 # 0 = start at the steady mix immediately.

    dup_model: str = "static"           # "static" | "coverage"
    item_universe_n: int = 500_000      # N — addressable item space (retail SKU proxy)
    initial_catalog_items: int = 30_000 # C_0 — pre-seeded catalogue at launch
                                        # (default ≈ testnet: 1,000 testers × 5 new
                                        # scans/mo × 6 months)
    popularity_weight: float = 1.0      # w — scan concentration factor (1 = uniform)


@dataclass
class DerivedMetrics:
    """
    All values derived from TokenomicsParams via derive_metrics().
    Downstream visualisations consume this object — they never recalculate.
    """
    community_pool_m: float     # Community pool tokens (millions)
    rc_m: float                 # Reserve Cushion at TGE (millions)
    mvt_m: float                # Monthly Vesting Tranche (millions)
    mms_m: float                # Maximum Monthly Spend (millions)

    # Category monthly budgets (millions of tokens, at MVT)
    scan_budget_m: float
    raffle_budget_m: float
    contributor_budget_m: float
    quests_budget_m: float

    # Base per-action rates at steady-state volume
    base_new_scan_tokens: float     # tokens per new scan (post-floor clamp)
    base_dup_tokens: float          # tokens per duplicate scan
    base_new_scan_eur: float        # EUR value per new scan at ICO price
    base_dup_eur: float

    # Effective per-action rates after hard cap is applied.
    # base_* = what the pool budget would produce at steady-state volume (pre-cap).
    # effective_* = min(base, hard_cap) — what contributors actually receive.
    # When hard cap < base rate, the difference flows to RC each month as savings.
    effective_new_scan_tokens: float
    effective_dup_tokens: float
    effective_new_scan_eur: float

    # C2 tier-multiplier blend (supervisor feedback, C2 point 2).
    # blended_* are volume-weighted across the C2 tier population with the hard
    # cap binding LAST. When multipliers are disabled these equal effective_*.
    blended_scan_mult: float          # volume-weighted avg multiplier (reporting)
    blended_new_scan_tokens: float    # what the average scan actually costs
    blended_dup_tokens: float
    blended_new_scan_eur: float

    # Testnet rates
    tn_new_scan_tokens: float
    tn_dup_tokens: float
    tn_monthly_budget_m: float      # AVERAGE monthly budget (pool / months)
    tn_tge_m: float                 # Testnet TGE unlock (millions, immediate)
    tn_vest_monthly_m: float        # Post-TGE testnet vesting per month (millions)


# ==============================================================================
# 3. CALCULATION ENGINE
# ==============================================================================

def tier_volume_shares(p: TokenomicsParams, month: int | None = None) -> list:
    """
    Share of monthly SCAN VOLUME attributable to each C2 tier.

    Two-step derivation:
      1. Population shares at this month. If tier_ramp_months > 0 the
         population starts all-Scout at launch and migrates linearly toward
         tier_mix_pct — tiers only ratchet upward (XP never decays), so the
         blended multiplier drifts up over time. month=None → steady mix.
      2. Volume weighting: share_i × intensity_i, renormalised. Cost follows
         scans, not headcount, and high tiers scan more.

    Returns a list of 6 volume shares summing to 1.0.
    """
    mix = list(p.tier_mix_pct)
    if len(mix) != len(C2_SCAN_MULT):
        mix = list(C2_DEFAULT_MIX)

    # --- 1. Population shares (with optional ramp) ---
    if month is not None and p.tier_ramp_months > 0:
        progress = min(1.0, month / float(p.tier_ramp_months))
        launch = [100.0] + [0.0] * (len(mix) - 1)     # everyone starts Scout
        pop = [(1.0 - progress) * launch[i] + progress * mix[i]
               for i in range(len(mix))]
    else:
        pop = mix

    # --- 2. Volume weighting ---
    intensity = list(p.tier_scan_intensity)
    if len(intensity) != len(mix):
        intensity = list(C2_DEFAULT_INTENSITY)

    weighted = [max(pop[i], 0.0) * max(intensity[i], 0.0) for i in range(len(mix))]
    total = sum(weighted)
    if total <= 0:
        return [1.0] + [0.0] * (len(mix) - 1)
    return [w / total for w in weighted]


def blended_scan_multiplier(p: TokenomicsParams, month: int | None = None) -> float:
    """
    Volume-weighted average C2 scan multiplier. Reporting metric only — the
    actual payout uses blended_effective_rate(), which is NOT base × this
    value once the hard cap starts binding.
    """
    if not p.enable_tier_multipliers:
        return 1.0
    shares = tier_volume_shares(p, month)
    return sum(shares[i] * C2_SCAN_MULT[i] for i in range(len(shares)))


def blended_effective_rate(p: TokenomicsParams,
                           base_rate: float,
                           month: int | None = None) -> float:
    """
    Volume-weighted effective $AVA per new scan, with the hard cap binding LAST:

        paid_i    = min(base_rate × mult_i, hard_cap)      per tier
        effective = Σ share_i × paid_i

    Cap-binds-last is the deliberate choice (see supervisor feedback, C2 pt 2):
    the hard cap is a true ceiling on what any single scan can pay, so C2
    multipliers can never breach C1's budget guardrail. Note the consequence —
    when base_rate ≥ hard_cap, every tier is capped and the multipliers cost
    nothing; they only bite once base_rate falls below the cap (i.e. as volume
    grows), which is exactly when the budget is tightest.
    """
    cap = float(p.hard_cap_new_scan)
    if not p.enable_tier_multipliers:
        return min(base_rate, cap)
    shares = tier_volume_shares(p, month)
    return sum(
        shares[i] * min(base_rate * C2_SCAN_MULT[i], cap)
        for i in range(len(shares))
    )


def derive_metrics(p: TokenomicsParams) -> DerivedMetrics:
    """
    Single source of truth for all derived numbers.
    Call this once per render cycle; pass the result to every chart function.

    Key formulas
    ------------
    community_pool = total_supply × community_pool_pct
    RC             = community_pool × tge_unlock_pct            (Reserve Cushion)
    MVT            = (community_pool − RC) / vesting_months     (Monthly Vesting Tranche)
    MMS            = MVT × mms_multiplier                       (Maximum Monthly Spend)

    Scan rate derivation
    --------------------
    Let x = tokens per new scan, x/D = tokens per duplicate scan (D = dup_divisor).
    Monthly scan cost = new_scans·x + dup_scans·(x/D)
                      = x · (new_scans + dup_scans/D)
    Setting this equal to scan_budget:
        x = scan_budget / (new_scans + dup_scans/D)
    x is then clamped to the user-defined floor.
    """

    # --- Supply pools ---
    community_pool_m = p.total_supply_m * (p.community_pool_pct / 100.0)
    rc_m             = community_pool_m * (p.tge_unlock_pct / 100.0)
    mvt_m            = (community_pool_m - rc_m) / max(p.vesting_months, 1)
    mms_m            = mvt_m * p.mms_multiplier

    # --- Category monthly budgets ---
    scan_budget_m        = mvt_m * (p.scan_pct        / 100.0)
    raffle_budget_m      = mvt_m * (p.raffle_pct      / 100.0)
    contributor_budget_m = mvt_m * (p.contributor_pct / 100.0)
    quests_budget_m      = mvt_m * (p.quests_pct      / 100.0)

    # --- Base per-scan rate ---
    scan_budget_tokens = scan_budget_m * 1_000_000.0
    denom = p.new_scans_monthly + p.dup_scans_monthly / max(p.dup_divisor, 1.0)

    if denom > 0:
        raw_new = scan_budget_tokens / denom
    else:
        raw_new = 0.0

    # Enforce floor guarantee (the floor applies even if volume is very high)
    base_new = max(raw_new, float(p.floor_new_scan))
    base_dup = base_new / max(p.dup_divisor, 1.0)

    # --- Apply hard cap ---
    # The hard cap limits the per-scan payout regardless of how generous the pool rate is.
    # Any scan budget not paid out due to the hard cap flows to RC (handled in simulation).
    eff_new = min(base_new, float(p.hard_cap_new_scan))
    eff_dup = eff_new / max(p.dup_divisor, 1.0)

    # --- Blend across C2 tier multipliers (cap binds last) ---
    # Steady-state mix (month=None) — the simulation re-blends per month so the
    # tier ramp is reflected over time.
    blended_mult = blended_scan_multiplier(p, None)
    blended_new  = blended_effective_rate(p, base_new, None)
    blended_dup  = blended_new / max(p.dup_divisor, 1.0)

    # --- Testnet rates ---
    tn_new = base_new * p.testnet_alpha
    tn_dup = base_dup * p.testnet_alpha
    tn_monthly_budget_m = p.testnet_pool_m / max(p.testnet_months, 1)

    # Testnet unlock structure (allocation sheet): testnet_tge_pct% unlocked at
    # TGE, remainder vesting linearly over the testnet window.
    tn_tge_m          = p.testnet_pool_m * (p.testnet_tge_pct / 100.0)
    tn_vest_monthly_m = (p.testnet_pool_m - tn_tge_m) / max(p.testnet_months, 1)

    return DerivedMetrics(
        community_pool_m          = community_pool_m,
        rc_m                      = rc_m,
        mvt_m                     = mvt_m,
        mms_m                     = mms_m,
        scan_budget_m             = scan_budget_m,
        raffle_budget_m           = raffle_budget_m,
        contributor_budget_m      = contributor_budget_m,
        quests_budget_m           = quests_budget_m,
        base_new_scan_tokens      = base_new,
        base_dup_tokens           = base_dup,
        base_new_scan_eur         = base_new * p.token_price_eur,
        base_dup_eur              = base_dup * p.token_price_eur,
        effective_new_scan_tokens = eff_new,
        effective_dup_tokens      = eff_dup,
        effective_new_scan_eur    = eff_new * p.token_price_eur,
        blended_scan_mult         = blended_mult,
        blended_new_scan_tokens   = blended_new,
        blended_dup_tokens        = blended_dup,
        blended_new_scan_eur      = blended_new * p.token_price_eur,
        tn_new_scan_tokens        = tn_new,
        tn_dup_tokens             = tn_dup,
        tn_monthly_budget_m       = tn_monthly_budget_m,
        tn_tge_m                  = tn_tge_m,
        tn_vest_monthly_m         = tn_vest_monthly_m,
    )


def run_simulation(p: TokenomicsParams, d: DerivedMetrics, n_months: int = 36) -> pd.DataFrame:
    """
    Month-by-month simulation of the token economy over n_months.

    Volume model
    ------------
    Three phases controlled by sidebar parameters:
      1. Spike phase   (months 1 … spike_months):         volume = base × spike_multiplier
      2. Decay phase   (months spike+1 … spike+decay):    linear decay → base × 1.0
      3. Steady state  (months spike+decay+1 … n_months): volume = base × 1.0

    New-vs-duplicate split (p.dup_model)
    ------------------------------------
    "static":   new and dup volumes are the sidebar inputs, both × vol multiplier.
    "coverage": the sidebar inputs define TOTAL scan demand; each month
                  new_scans = (N − C_t) · (1 − exp(−S_t / (w·N)))
                  dup_scans = S_t − new_scans;   C_{t+1} = C_t + new_scans
                (coupon-collector integral of dup_prob = C_t/N, so a month's
                scans overlap each other as well as the existing catalogue).
                Discovery saturates smoothly as the catalogue fills.

    Budget model (per month)
    ------------------------
    For each month:
      a. Apply hard cap to base scan rate:
           hc_rate = min(base_new_scan_tokens, hard_cap_new_scan)
           hc_dup  = hc_rate / D
         Hard cap savings = (base_rate − hc_rate) × effective_volume → added to RC.
      b. Compute scan accruals at hard-capped rates.
      c. Non-scan categories always spend their fixed MVT budget.
      d. total_accruals = hard-capped scan accruals + non-scan budgets
      e. Soft cap fires if total_accruals > MMS:
           scaling = MMS / total_accruals  (all categories scaled equally)
      f. actual_spend   = total_accruals × scaling
      g. RC dynamics each month:
           RC_drawdown      = max(0, actual_spend − MVT)   — RC funds excess over MVT
           MVT_rollover     = max(0, MVT − actual_spend)   — unspent MVT refills RC
           hardcap_savings  = (base_rate − hc_rate) × volume — unused scan budget → RC
           RC_balance       = RC − drawdown + rollover + hardcap_savings
           Bounded: RC ∈ [0, initial_RC]  (cannot exceed original TGE amount)
      h. effective rate = max(hc_rate × scaling, floor)

    Returns a DataFrame with one row per month, all quantities annotated.
    """
    rows       = []
    rc_balance = d.rc_m          # RC starts at the full TGE unlock amount
    rc_cap     = d.rc_m          # RC cannot exceed its initial TGE size

    # Fixed non-scan monthly budgets (tokens)
    raffle_fixed      = d.raffle_budget_m      * 1_000_000.0
    contributor_fixed = d.contributor_budget_m * 1_000_000.0
    quests_fixed      = d.quests_budget_m      * 1_000_000.0
    non_scan_fixed    = raffle_fixed + contributor_fixed + quests_fixed

    mvt_tokens = d.mvt_m * 1_000_000.0
    mms_tokens = d.mms_m * 1_000_000.0

    # Hard-capped scan rates (applied BEFORE the soft cap and BEFORE floor).
    # NOTE: with C2 multipliers enabled these are re-blended per month inside
    # the loop (tier population ramps up over time), so they act as the
    # month-0 / multipliers-off baseline here.
    hc_new_rate = min(d.base_new_scan_tokens, float(p.hard_cap_new_scan))
    hc_dup_rate = hc_new_rate / max(p.dup_divisor, 1.0)

    # Coverage-model state: cumulative catalogue size (tracked in both modes
    # so the Coverage % column is always meaningful).
    universe_n       = max(p.item_universe_n, 1)
    catalog          = float(min(p.initial_catalog_items, universe_n))
    total_base_scans = p.new_scans_monthly + p.dup_scans_monthly

    for month in range(1, n_months + 1):

        # ---- 1. Volume multiplier for this month ----
        if month <= p.spike_months:
            vol_mult = p.spike_multiplier
        elif month <= p.spike_months + p.decay_months:
            progress = (month - p.spike_months) / max(p.decay_months, 1)
            vol_mult = p.spike_multiplier + progress * (1.0 - p.spike_multiplier)
        else:
            vol_mult = 1.0

        # ---- 1b. New-vs-duplicate split ----
        if p.dup_model == "coverage":
            total_scans = total_base_scans * vol_mult
            remaining   = max(universe_n - catalog, 0.0)
            # Coupon-collector form: within a month, scans overlap both the
            # existing catalogue and each other. w > 1 concentrates scans on
            # popular items, shrinking effective discovery breadth to S/w.
            new_scans = remaining * (1.0 - np.exp(
                -total_scans / (max(p.popularity_weight, 1.0) * universe_n)
            ))
            dup_scans = total_scans - new_scans
        else:
            new_scans = p.new_scans_monthly * vol_mult
            dup_scans = p.dup_scans_monthly * vol_mult

        catalog = min(catalog + new_scans, float(universe_n))

        # ---- 1c. C2 tier blend for this month (cap binds last) ----
        # The tier population ramps upward over time, so the blended rate — and
        # therefore the cost of a scan — drifts up even at constant volume.
        month_new_rate = blended_effective_rate(p, d.base_new_scan_tokens, month)
        month_dup_rate = month_new_rate / max(p.dup_divisor, 1.0)
        month_mult     = blended_scan_multiplier(p, month)

        # ---- 2. Hard cap savings (tokens not paid out → flow to RC) ----
        # Difference between what the pool rate would pay and what is actually
        # paid after multipliers and the hard cap. Multipliers consume this
        # headroom, so savings shrink as the population matures.
        uncapped_new = d.base_new_scan_tokens
        uncapped_dup = d.base_dup_tokens
        hc_savings = max(0.0, (
            (uncapped_new - month_new_rate) * new_scans
            + (uncapped_dup - month_dup_rate) * dup_scans
        ))   # in raw tokens

        # ---- 3. Scan accruals at blended, hard-capped rates ----
        raw_scan_accruals = (
            new_scans * month_new_rate
            + dup_scans * month_dup_rate
        )

        # ---- 4. Total monthly accruals ----
        total_accruals = raw_scan_accruals + non_scan_fixed

        # ---- 5. Soft cap check ----
        if total_accruals > mms_tokens:
            scaling   = mms_tokens / total_accruals
            cap_fired = True
        else:
            scaling   = 1.0
            cap_fired = False

        # ---- 6. Actual spend (after soft cap) ----
        actual_spend      = total_accruals * scaling
        actual_scan_spend = raw_scan_accruals * scaling

        # ---- 7. Effective per-scan rate (soft-cap-scaled, then floor-clamped) ----
        eff_new_scan = max(month_new_rate * scaling, float(p.floor_new_scan))
        eff_dup      = max(month_dup_rate * scaling, p.floor_new_scan / max(p.dup_divisor, 1.0))
        eff_new_eur  = eff_new_scan * p.token_price_eur

        # ---- 8. RC dynamics ----
        rc_drawdown      = max(0.0, actual_spend   - mvt_tokens)
        mvt_rollover     = max(0.0, mvt_tokens     - actual_spend)
        hc_savings_m     = hc_savings / 1_000_000.0          # convert to millions

        rc_balance = rc_balance - rc_drawdown / 1e6 + mvt_rollover / 1e6 + hc_savings_m
        rc_balance = max(0.0, min(rc_balance, rc_cap))    # bounded

        # ---- Record row ----
        rows.append({
            "Month":                   month,
            "Vol Multiplier":          round(vol_mult, 2),
            "New Scans":               int(new_scans),
            "Dup Scans":               int(dup_scans),
            "Catalog Size":            int(catalog),
            "Coverage %":              round(100.0 * catalog / universe_n, 2),
            # C2 tier blend
            "Blended Tier Mult":       round(month_mult, 3),
            "Blended New Rate":        round(month_new_rate, 1),
            # Token flows (millions)
            "Scan Accruals (M)":       round(raw_scan_accruals / 1e6, 3),
            "Total Accruals (M)":      round(total_accruals    / 1e6, 3),
            "Actual Spend (M)":        round(actual_spend      / 1e6, 3),
            "MVT (M)":                 round(mvt_tokens        / 1e6, 3),
            "MMS (M)":                 round(mms_tokens        / 1e6, 3),
            # RC
            "RC Drawdown (M)":         round(rc_drawdown / 1e6,  3),
            "MVT Rollover (M)":        round(mvt_rollover / 1e6, 3),
            "HC Savings to RC (M)":    round(hc_savings_m,       3),
            "RC Balance (M)":          round(rc_balance,         3),
            # Soft cap
            "Soft Cap Fired":          cap_fired,
            "Scaling Factor":          round(scaling, 4),
            # Per-scan rates
            "Eff New Scan Tokens":     round(eff_new_scan, 1),
            "Eff Dup Tokens":          round(eff_dup,      2),
            "Eff New Scan EUR":        round(eff_new_eur,  4),
            # Category spend (millions)
            "Scan Spend (M)":          round(actual_scan_spend          / 1e6, 3),
            "Raffle Spend (M)":        round(raffle_fixed      * scaling / 1e6, 3),
            "Contributor Spend (M)":   round(contributor_fixed * scaling / 1e6, 3),
            "Quests Spend (M)":        round(quests_fixed      * scaling / 1e6, 3),
        })

    return pd.DataFrame(rows)


# ==============================================================================
# 4. CHART FUNCTIONS
# ==============================================================================

def fig_bucket_donut(p: TokenomicsParams, d: DerivedMetrics) -> go.Figure:
    """
    Donut chart: monthly budget split across the four reward categories.
    Hovering shows the absolute token budget as well as the percentage.
    """
    labels  = ["Scan-to-Earn", "Raffle", "Contributor Tracks", "Quests & Campaigns"]
    pcts    = [p.scan_pct, p.raffle_pct, p.contributor_pct, p.quests_pct]
    budgets = [d.scan_budget_m, d.raffle_budget_m, d.contributor_budget_m, d.quests_budget_m]

    fig = go.Figure(go.Pie(
        labels=labels,
        values=pcts,
        hole=0.50,
        marker_colors=BUCKET_COLORS,
        textinfo="label+percent",
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Share: %{percent}<br>"
            "Monthly budget: %{customdata:.3f}M $AVA<extra></extra>"
        ),
        customdata=budgets,
    ))
    fig.update_layout(
        title="Monthly Budget Allocation (% of MVT)",
        showlegend=False,
        margin=dict(t=50, b=10, l=10, r=10),
        height=340,
    )
    return fig


def fig_rc_runway(sim_df: pd.DataFrame, d: DerivedMetrics) -> go.Figure:
    """
    Line chart: Reserve Cushion (RC) balance over 36 months.
    Gold ✕ markers indicate months where the soft cap fired.
    Horizontal reference lines show MVT and the zero level.
    """
    fired   = sim_df["Soft Cap Fired"]

    fig = go.Figure()

    # ---- RC balance line ----
    fig.add_trace(go.Scatter(
        x=sim_df["Month"],
        y=sim_df["RC Balance (M)"],
        mode="lines",
        name="RC Balance",
        line=dict(color=COLORS["rc_line"], width=2.5),
        hovertemplate="Month %{x}<br>RC Balance: %{y:.2f}M $AVA<extra></extra>",
    ))

    # ---- Soft-cap-fired markers ----
    if fired.any():
        fig.add_trace(go.Scatter(
            x=sim_df.loc[fired, "Month"],
            y=sim_df.loc[fired, "RC Balance (M)"],
            mode="markers",
            name="Soft Cap Fired",
            marker=dict(color=COLORS["cap_marker"], size=11, symbol="x-thin",
                        line=dict(width=2.5, color=COLORS["cap_marker"])),
            hovertemplate="Month %{x}: soft cap fired<extra></extra>",
        ))

    # ---- Reference lines ----
    fig.add_hline(
        y=d.mvt_m, line_dash="dot", line_color=COLORS["mvt"], opacity=0.7,
        annotation_text=f"MVT = {d.mvt_m:.2f}M/mo",
        annotation_position="bottom right",
        annotation_font_color=COLORS["mvt"],
    )
    fig.add_hline(y=0, line_color="red", line_width=1, opacity=0.4)

    fig.update_layout(
        title="Reserve Cushion Balance Over 36 Months",
        xaxis_title="Month",
        yaxis_title="RC Balance (M $AVA)",
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=55, b=70, l=65, r=20),
        height=380,
    )
    return fig


def fig_scan_rate_curve(sim_df: pd.DataFrame, p: TokenomicsParams, d: DerivedMetrics) -> go.Figure:
    """
    Dual-axis line chart:
      Left axis  — effective new scan reward in tokens over time.
      Right axis — EUR equivalent (at ICO price).
    Orange floor line and yellow shading on cap-fired months.
    """
    fired = sim_df["Soft Cap Fired"]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ---- Token rate (left axis) ----
    fig.add_trace(go.Scatter(
        x=sim_df["Month"],
        y=sim_df["Eff New Scan Tokens"],
        mode="lines+markers",
        name="$AVA / new scan",
        line=dict(color=COLORS["scan"], width=2.5),
        marker=dict(size=5),
        hovertemplate="Month %{x}<br>%{y:.0f} $AVA<extra></extra>",
    ), secondary_y=False)

    # ---- EUR equivalent (right axis) ----
    fig.add_trace(go.Scatter(
        x=sim_df["Month"],
        y=sim_df["Eff New Scan EUR"],
        mode="lines",
        name="EUR / new scan",
        line=dict(color=COLORS["contributor"], width=1.5, dash="dash"),
        hovertemplate="Month %{x}<br>€%{y:.3f}<extra></extra>",
    ), secondary_y=True)

    # ---- Floor line ----
    fig.add_hline(
        y=p.floor_new_scan, line_dash="dash",
        line_color=COLORS["floor"], opacity=0.8,
        annotation_text=f"Floor = {p.floor_new_scan} $AVA",
        annotation_position="bottom right",
        annotation_font_color=COLORS["floor"],
    )

    # ---- Shade soft-cap months ----
    for _, row in sim_df[fired].iterrows():
        fig.add_vrect(
            x0=row["Month"] - 0.45, x1=row["Month"] + 0.45,
            fillcolor=COLORS["cap_marker"], opacity=0.12, line_width=0,
        )

    fig.update_yaxes(title_text="$AVA per new scan",  secondary_y=False)
    fig.update_yaxes(title_text="EUR value at ICO price", secondary_y=True)
    fig.update_layout(
        title="Effective New Scan Reward Over Time  (shaded = soft cap month)",
        xaxis_title="Month",
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=55, b=70, l=65, r=75),
        height=380,
    )
    return fig


def fig_monthly_spend_stack(sim_df: pd.DataFrame) -> go.Figure:
    """
    Stacked bar chart: actual token spend per category each month.
    MMS and MVT reference lines overlay the bars.
    """
    fig = go.Figure()

    # ---- Category bars (stacked) ----
    category_traces = [
        ("Scan Spend (M)",        "Scan-to-Earn",        COLORS["scan"]),
        ("Raffle Spend (M)",      "Raffle",              COLORS["raffle"]),
        ("Contributor Spend (M)", "Contributor Tracks",  COLORS["contributor"]),
        ("Quests Spend (M)",      "Quests & Campaigns",  COLORS["quests"]),
    ]
    for col, name, color in category_traces:
        fig.add_trace(go.Bar(
            x=sim_df["Month"], y=sim_df[col],
            name=name, marker_color=color,
            hovertemplate=f"{name}: %{{y:.3f}}M $AVA<extra></extra>",
        ))

    # ---- MMS cap line ----
    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=sim_df["MMS (M)"],
        mode="lines", name="MMS (cap)",
        line=dict(color=COLORS["mms"], dash="dash", width=2),
        hovertemplate="MMS: %{y:.2f}M<extra></extra>",
    ))

    # ---- MVT steady-state line ----
    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=sim_df["MVT (M)"],
        mode="lines", name="MVT (steady state)",
        line=dict(color=COLORS["mvt"], dash="dot", width=2),
        hovertemplate="MVT: %{y:.2f}M<extra></extra>",
    ))

    fig.update_layout(
        barmode="stack",
        title="Monthly $AVA Spend by Category",
        xaxis_title="Month",
        yaxis_title="$AVA (millions)",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=90, l=65, r=20),
        height=430,
    )
    return fig


def fig_volume_profile(sim_df: pd.DataFrame) -> go.Figure:
    """
    Grouped bar chart: new scans vs. duplicate scans each month.
    Useful for verifying the launch curve shape set in the sidebar.
    """
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=sim_df["Month"], y=sim_df["New Scans"],
        name="New scans", marker_color=COLORS["scan"],
        hovertemplate="Month %{x}<br>New: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=sim_df["Month"], y=sim_df["Dup Scans"],
        name="Duplicate scans", marker_color=COLORS["contributor"], opacity=0.7,
        hovertemplate="Month %{x}<br>Dup: %{y:,}<extra></extra>",
    ))
    fig.update_layout(
        barmode="group",
        title="Scan Volume Profile (launch curve)",
        xaxis_title="Month",
        yaxis_title="Number of scans",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=80, l=65, r=20),
        height=340,
    )
    return fig


def fig_tier_multiplier_curve(sim_df: pd.DataFrame, p: TokenomicsParams,
                              d: DerivedMetrics) -> go.Figure:
    """
    Dual-axis chart for the C2 tier-multiplier blend:
      Left axis  — blended volume-weighted multiplier as the population matures.
      Right axis — resulting blended $AVA per new scan, vs the flat-rate
                   (multipliers-off) baseline and the hard cap.

    Makes the two effects visible: the ramp (cost drifts up at constant volume)
    and cap saturation (the gap between blended and flat collapses to zero
    whenever the pool rate sits above the hard cap).
    """
    flat_rate = min(d.base_new_scan_tokens, float(p.hard_cap_new_scan))

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=sim_df["Blended Tier Mult"],
        mode="lines+markers", name="Blended tier multiplier",
        line=dict(color=COLORS["contributor"], width=2.5), marker=dict(size=4),
        hovertemplate="Month %{x}<br>Blended mult: %{y:.3f}×<extra></extra>",
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=sim_df["Blended New Rate"],
        mode="lines", name="Blended $AVA / new scan",
        line=dict(color=COLORS["scan"], width=2.5),
        hovertemplate="Month %{x}<br>%{y:,.0f} $AVA<extra></extra>",
    ), secondary_y=True)

    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=[flat_rate] * len(sim_df),
        mode="lines", name="Flat rate (multipliers off)",
        line=dict(color=COLORS["neutral"], width=1.5, dash="dot"),
        hovertemplate="Flat: %{y:,.0f} $AVA<extra></extra>",
    ), secondary_y=True)

    fig.add_hline(
        y=p.hard_cap_new_scan, line_dash="dash", line_color=COLORS["mms"],
        opacity=0.8, secondary_y=True,
        annotation_text=f"Hard cap = {p.hard_cap_new_scan:,}",
        annotation_position="top right",
        annotation_font_color=COLORS["mms"],
    )

    fig.update_yaxes(title_text="Blended multiplier (×)", secondary_y=False)
    fig.update_yaxes(title_text="$AVA per new scan", secondary_y=True,
                     rangemode="tozero")
    fig.update_layout(
        title=("C2 Tier Multiplier Blend — Cost Drift as the Population Matures"
               if p.enable_tier_multipliers else
               "C2 Tier Multipliers — DISABLED (flat-rate pricing)"),
        xaxis_title="Month",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=80, l=65, r=75),
        height=360,
    )
    return fig


def fig_coverage_curve(sim_df: pd.DataFrame, p: TokenomicsParams) -> go.Figure:
    """
    Dual-axis chart for the coverage-based duplicate model:
      Left axis  — catalogue coverage % (C_t / N) over time.
      Right axis — duplicate share of total scans per month.
    Shows discovery saturating: coverage climbs, duplicate share rises with it.
    """
    total = (sim_df["New Scans"] + sim_df["Dup Scans"]).clip(lower=1)
    dup_share = 100.0 * sim_df["Dup Scans"] / total

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=sim_df["Coverage %"],
        mode="lines+markers", name="Catalogue coverage (Cₜ/N)",
        line=dict(color=COLORS["mvt"], width=2.5), marker=dict(size=5),
        hovertemplate="Month %{x}<br>Coverage: %{y:.1f}%<extra></extra>",
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        x=sim_df["Month"], y=dup_share,
        mode="lines", name="Duplicate share of scans",
        line=dict(color=COLORS["contributor"], width=2, dash="dash"),
        hovertemplate="Month %{x}<br>Dup share: %{y:.1f}%<extra></extra>",
    ), secondary_y=True)

    fig.update_yaxes(title_text="Coverage % of item universe", secondary_y=False,
                     rangemode="tozero")
    fig.update_yaxes(title_text="Duplicate share of scans (%)", secondary_y=True,
                     rangemode="tozero")
    fig.update_layout(
        title=(f"Catalogue Coverage & Duplicate Share  "
               f"(N = {p.item_universe_n:,}, w = {p.popularity_weight:g})"),
        xaxis_title="Month",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=80, l=65, r=75),
        height=340,
    )
    return fig


def fig_rc_sources(sim_df: pd.DataFrame) -> go.Figure:
    """
    Stacked area chart showing month-by-month RC inflows (MVT rollover) 
    vs. outflows (RC drawdown), and net RC balance.
    Helps understand when RC is being built vs. depleted.
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ---- Drawdown (negative) ----
    fig.add_trace(go.Bar(
        x=sim_df["Month"],
        y=-sim_df["RC Drawdown (M)"],           # negative → drawdown from RC
        name="RC Drawdown",
        marker_color=COLORS["floor"],
        opacity=0.7,
        hovertemplate="Month %{x}<br>Drawdown: %{customdata:.3f}M<extra></extra>",
        customdata=sim_df["RC Drawdown (M)"],
    ), secondary_y=False)

    # ---- MVT Rollover (positive) ----
    fig.add_trace(go.Bar(
        x=sim_df["Month"],
        y=sim_df["MVT Rollover (M)"],            # positive → unspent MVT fills RC
        name="MVT Rollover (RC top-up)",
        marker_color=COLORS["mvt"],
        opacity=0.7,
        hovertemplate="Month %{x}<br>Rollover: %{y:.3f}M<extra></extra>",
    ), secondary_y=False)

    # ---- RC balance (line, right axis) ----
    fig.add_trace(go.Scatter(
        x=sim_df["Month"],
        y=sim_df["RC Balance (M)"],
        mode="lines",
        name="RC Balance",
        line=dict(color=COLORS["rc_line"], width=2.5),
        hovertemplate="Month %{x}<br>RC Balance: %{y:.2f}M<extra></extra>",
    ), secondary_y=True)

    fig.update_yaxes(title_text="Monthly RC flow (M $AVA)",  secondary_y=False)
    fig.update_yaxes(title_text="RC Balance (M $AVA)",       secondary_y=True)
    fig.update_layout(
        barmode="relative",
        title="RC Inflows & Outflows (bars) vs. RC Balance (line)",
        xaxis_title="Month",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=90, l=65, r=75),
        height=400,
    )
    return fig


def fig_rc_combined_timeline(
    p: TokenomicsParams,
    d: DerivedMetrics,
    sim_df: pd.DataFrame,
) -> go.Figure:
    """
    Combined RC balance chart spanning both testnet and public (post-ICO) phases
    on a single continuous x-axis.

    Left segment  — Testnet phase:
      Simulates the testnet pool depletion month-by-month using
      p.testnet_chart_users × (5 new scans + 20 dup scans per user per month)
      at testnet rates (α-scaled).  Available capital starts at the TGE unlock
      (testnet_tge_pct% of the pool) and grows by the monthly vesting tranche.

    Transition    — "0 (ICO)":
      Vertical dashed line marks the TGE / public launch point.
      Mainnet RC starts fresh at d.rc_m (independent of testnet pool).

    Right segment — Public phase (months 1–36):
      RC balance taken directly from sim_df, which already incorporates
      hard cap savings, MVT rollover, and soft cap dynamics.

    X-axis labels:  "T M0", "T M1", …, "T MN", "0 (ICO)", "M1", …, "M36"
    """
    scans_per_user_new = 5
    scans_per_user_dup = 20

    # --- Simulate testnet pool depletion ---
    monthly_tn_spend = (
        p.testnet_chart_users * scans_per_user_new * d.tn_new_scan_tokens
        + p.testnet_chart_users * scans_per_user_dup * d.tn_dup_tokens
    ) / 1_000_000.0

    # Unlock structure per allocation sheet: tn_tge_m available at T M0,
    # remainder vesting in monthly tranches — NOT the full pool up front.
    tn_balances = [d.tn_tge_m]                # T M0 = TGE unlock only
    bal = d.tn_tge_m
    for _ in range(p.testnet_months):
        bal = max(0.0, bal + d.tn_vest_monthly_m - monthly_tn_spend)
        tn_balances.append(bal)               # T M1 … T MN, then ICO point

    # --- Build unified x-axis labels ---
    tn_labels   = [f"T M{i}" for i in range(p.testnet_months + 1)]   # T M0 … T MN
    ico_label   = "0 (ICO)"
    pub_labels  = [f"M{i}" for i in range(1, len(sim_df) + 1)]       # M1 … M36

    # Testnet x: indices 0 … testnet_months
    # ICO x:     index  testnet_months + 1
    # Public x:  indices testnet_months + 2 … testnet_months + 1 + len(sim_df)
    ico_idx    = p.testnet_months + 1
    pub_start  = ico_idx + 1
    n_pub      = len(sim_df)

    all_labels = tn_labels + [ico_label] + pub_labels
    all_x      = list(range(len(all_labels)))

    tn_x     = list(range(len(tn_labels)))                          # 0 … testnet_months
    # Testnet ends at ICO — carry last value to the ICO tick
    tn_y_ext = tn_balances + [tn_balances[-1]]                      # one extra for ICO col
    tn_x_ext = tn_x        + [ico_idx]

    pub_x   = list(range(ico_idx, ico_idx + n_pub + 1))            # ICO col + M1…M36
    pub_y   = [d.rc_m] + sim_df["RC Balance (M)"].tolist()          # RC starts at d.rc_m at ICO

    fig = go.Figure()

    # ---- Testnet pool balance ----
    fig.add_trace(go.Scatter(
        x=tn_x_ext,
        y=tn_y_ext,
        mode="lines+markers",
        name="Testnet pool balance",
        line=dict(color=COLORS["contributor"], width=2.5),
        marker=dict(size=5),
        hovertemplate="<b>%{customdata}</b><br>Testnet pool: %{y:.2f}M $AVA<extra></extra>",
        customdata=[all_labels[i] for i in tn_x_ext],
    ))

    # ---- Mainnet RC balance ----
    fig.add_trace(go.Scatter(
        x=pub_x,
        y=pub_y,
        mode="lines+markers",
        name="Mainnet RC balance",
        line=dict(color=COLORS["rc_line"], width=2.5),
        marker=dict(size=5),
        hovertemplate="<b>%{customdata}</b><br>Mainnet RC: %{y:.2f}M $AVA<extra></extra>",
        customdata=[all_labels[i] for i in pub_x],
    ))

    # ---- ICO transition line ----
    fig.add_vline(
        x=ico_idx, line_dash="dash", line_color=COLORS["mms"], line_width=2,
        annotation_text="TGE / ICO",
        annotation_position="top right",
        annotation_font_color=COLORS["mms"],
    )

    # ---- MVT reference ----
    fig.add_hline(
        y=d.mvt_m, line_dash="dot", line_color=COLORS["mvt"], opacity=0.6,
        annotation_text=f"MVT {d.mvt_m:.2f}M/mo",
        annotation_position="bottom right",
        annotation_font_color=COLORS["mvt"],
    )

    # ---- Custom tick labels (show every tick for testnet, sparse for public) ----
    tick_indices = list(range(len(tn_labels))) + [ico_idx]          # all testnet + ICO
    # Public phase: show M1, M6, M12, M18, M24, M30, M36
    pub_tick_offsets = [1, 6, 12, 18, 24, 30, 36]
    for off in pub_tick_offsets:
        idx = ico_idx + off
        if idx < len(all_labels):
            tick_indices.append(idx)

    tick_vals  = tick_indices
    tick_texts = [all_labels[i] for i in tick_indices]

    fig.update_xaxes(
        tickmode="array",
        tickvals=tick_vals,
        ticktext=tick_texts,
        tickangle=-40,
    )

    fig.update_layout(
        title="RC Balance: Testnet Phase → ICO → Public Phase (36 months)",
        xaxis_title="Timeline",
        yaxis_title="Balance (M $AVA)",
        legend=dict(orientation="h", y=-0.3),
        margin=dict(t=60, b=100, l=65, r=20),
        height=440,
    )
    return fig


def fig_testnet_burn(p: TokenomicsParams, d: DerivedMetrics) -> go.Figure:
    """
    Line chart showing how fast the testnet pool depletes under different
    active tester counts. Each line = a different tester count scenario.
    A horizontal reference marks the end of the testnet window.
    """
    tester_scenarios = [100, 500, 1_000, 2_000, 5_000, 10_000]
    # Assume each active tester does ~5 new scans + 20 dup scans per month
    scans_per_tester_new = 5
    scans_per_tester_dup = 20

    fig = go.Figure()

    for testers in tester_scenarios:
        monthly_spend = (
            testers * scans_per_tester_new * d.tn_new_scan_tokens
            + testers * scans_per_tester_dup * d.tn_dup_tokens
        ) / 1e6   # convert to millions

        # Available capital = TGE unlock + vesting tranches (within the window),
        # minus spend. Vesting stops after the testnet window ends.
        balance = d.tn_tge_m
        balances = []
        for m in range(1, p.testnet_months + 4):   # a few months beyond window
            inflow = d.tn_vest_monthly_m if m <= p.testnet_months else 0.0
            balance = max(0.0, balance + inflow - monthly_spend)
            balances.append(round(balance, 2))

        months = list(range(1, len(balances) + 1))
        fig.add_trace(go.Scatter(
            x=months, y=balances,
            mode="lines",
            name=f"{testers:,} testers",
            hovertemplate=f"{testers:,} testers — Month %{{x}}: %{{y:.1f}}M $AVA left<extra></extra>",
        ))

    # Testnet window boundary
    fig.add_vline(
        x=p.testnet_months, line_dash="dash", line_color=COLORS["mms"],
        annotation_text="End of testnet window",
        annotation_position="top right",
        annotation_font_color=COLORS["mms"],
    )
    fig.add_hline(y=0, line_color="red", opacity=0.4, line_width=1)

    fig.update_layout(
        title=f"Testnet Pool Depletion by Tester Count  (α = {p.testnet_alpha}×)",
        xaxis_title="Month",
        yaxis_title="Testnet pool remaining (M $AVA)",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=90, l=65, r=20),
        height=380,
    )
    return fig


def build_rate_table(p: TokenomicsParams, d: DerivedMetrics) -> pd.DataFrame:
    """
    Summary table of per-action token rates shown in the Overview tab.
    Extend this function to add new action types (e.g. verification bonus,
    referral reward) once those are defined in Component 2.
    """
    rows = [
        {
            "Action":             "New product scan",
            "Category":           "Scan-to-Earn",
            "$AVA (base)":        f"{d.base_new_scan_tokens:,.0f}",
            "EUR at ICO":         f"€{d.base_new_scan_eur:.2f}",
            "Testnet $AVA (α)":   f"{d.tn_new_scan_tokens:,.0f}",
            "Notes":              "Pool-derived rate at steady-state vol; hard cap applies below",
        },
        {
            "Action":             "New scan — effective rate (after hard cap)",
            "Category":           "Scan-to-Earn",
            "$AVA (base)":        f"{d.effective_new_scan_tokens:,.0f}",
            "EUR at ICO":         f"€{d.effective_new_scan_eur:.2f}",
            "Testnet $AVA (α)":   f"{d.effective_new_scan_tokens * p.testnet_alpha:,.0f}",
            "Notes":              f"min(pool rate, hard cap {p.hard_cap_new_scan:,}); this is what contributors earn",
        },
        {
            "Action":             "New scan — blended rate (C2 tiers applied)",
            "Category":           "Scan-to-Earn",
            "$AVA (base)":        f"{d.blended_new_scan_tokens:,.0f}",
            "EUR at ICO":         f"€{d.blended_new_scan_eur:.2f}",
            "Testnet $AVA (α)":   f"{d.blended_new_scan_tokens * p.testnet_alpha:,.0f}",
            "Notes":              (
                f"Volume-weighted across C2 tiers (blended {d.blended_scan_mult:.2f}×), "
                f"hard cap applied per tier LAST — this is the true average cost"
                if p.enable_tier_multipliers else
                "C2 multipliers disabled — equals the effective rate above"
            ),
        },
        {
            "Action":             "Duplicate scan (verification)",
            "Category":           "Scan-to-Earn",
            "$AVA (base)":        f"{d.base_dup_tokens:,.1f}",
            "EUR at ICO":         f"€{d.base_dup_eur:.4f}",
            "Testnet $AVA (α)":   f"{d.tn_dup_tokens:,.1f}",
            "Notes":              f"1/{p.dup_divisor:.0f}× of new scan rate",
        },
        {
            "Action":             "Floor — new scan (minimum guarantee)",
            "Category":           "Scan-to-Earn",
            "$AVA (base)":        f"{p.floor_new_scan:,}",
            "EUR at ICO":         f"€{p.floor_new_scan * p.token_price_eur:.3f}",
            "Testnet $AVA (α)":   f"{p.floor_new_scan * p.testnet_alpha:,.0f}",
            "Notes":              "Always honoured, even at MMS ceiling",
        },
        {
            "Action":             "Raffle prize pool (monthly total)",
            "Category":           "Raffle",
            "$AVA (base)":        f"{d.raffle_budget_m * 1e6:,.0f}",
            "EUR at ICO":         f"€{d.raffle_budget_m * 1e6 * p.token_price_eur:,.0f}",
            "Testnet $AVA (α)":   f"{d.raffle_budget_m * 1e6 * p.testnet_alpha:,.0f}",
            "Notes":              "Distributed across winners; per-prize defined in Comp 6",
        },
        {
            "Action":             "Contributor track budget (monthly total)",
            "Category":           "Contributor Tracks",
            "$AVA (base)":        f"{d.contributor_budget_m * 1e6:,.0f}",
            "EUR at ICO":         f"€{d.contributor_budget_m * 1e6 * p.token_price_eur:,.0f}",
            "Testnet $AVA (α)":   f"{d.contributor_budget_m * 1e6 * p.testnet_alpha:,.0f}",
            "Notes":              "Per-level XP thresholds defined in Comp 2",
        },
        {
            "Action":             "Quests budget (monthly total)",
            "Category":           "Quests & Campaigns",
            "$AVA (base)":        f"{d.quests_budget_m * 1e6:,.0f}",
            "EUR at ICO":         f"€{d.quests_budget_m * 1e6 * p.token_price_eur:,.0f}",
            "Testnet $AVA (α)":   f"{d.quests_budget_m * 1e6 * p.testnet_alpha:,.0f}",
            "Notes":              "Per-quest reward defined in Comp 2",
        },
    ]
    return pd.DataFrame(rows)


# ==============================================================================
# 4b. RAFFLE ENGINE (Component 6)
# ==============================================================================
#
# IMPORTANT MENTAL MODEL — two loosely-coupled simulations live here:
#
#   (1) Token distribution: the raffle prize pool is a FIXED 7% of MVT
#       (~931K $AVA/mo). It is independent of scan volume; scans only touch it
#       indirectly when the Component-1 soft cap fires and scales all spend down.
#
#   (2) Tickets & probabilities: driven entirely by ENGAGEMENT, not scanning.
#       Per the C6 spec §3, scanning earns $AVA but ZERO tickets. Tickets come
#       from streaks, quests, corrections, referrals, level-ups, multiplied by
#       the Component-2 tier (Scout 1.0× → Legend 3.0×).
#
# The per-month grid therefore carries scan columns (for context / token side)
# and user/commit columns (which actually drive the ticket pool T).
#
# Tier structure below is LOCKED per the C6 design spec (§5). Exposed as module
# constants rather than sidebar widgets to keep inputs clean; tweak here to
# stress-test structural parameters.

# --- Prize tier budget split (% of monthly raffle pool) ---
RAFFLE_JACKPOT_PCT = 22.0
RAFFLE_MID_PCT     = 32.0
RAFFLE_CONS_PCT    = 43.0
RAFFLE_BUFFER_PCT  = 3.0

# --- Jackpot (fixed) ---
RAFFLE_JACKPOT_FIXED = 200_000   # per-prize floor = ceiling = 200K $AVA, 1 winner

# --- Mid tier: winners = MID_COEF × N_active, clamped; per-prize clamped ---
RAFFLE_MID_COEF      = 0.01
RAFFLE_MID_FLOOR     = 20
RAFFLE_MID_CEIL      = 500
RAFFLE_MID_PP_FLOOR  = 600
RAFFLE_MID_PP_CEIL   = 15_000

# --- Consolation tier ---
RAFFLE_CONS_COEF     = 0.15
RAFFLE_CONS_FLOOR    = 200
RAFFLE_CONS_CEIL     = 5_000
RAFFLE_CONS_PP_FLOOR = 80
RAFFLE_CONS_PP_CEIL  = 2_000

# --- Pity mechanism (C6 §8) ---
# Revised 3 → 2 in July 2026. The original threshold assumed a 90-day ticket
# expiry; once expiry was set to 60 days, a three-month losing streak outlived
# the tickets earned at its start. Two draws keeps the guarantee inside the
# ticket lifetime.
RAFFLE_PITY_THRESHOLD = 2        # consecutive losing draws → guaranteed Consolation slot

# --- Ticket expiry (C6 §4) ---
RAFFLE_TICKET_EXPIRY_DAYS = 60   # rolling FIFO; uncommitted tickets burn after this
RAFFLE_DRAW_PERIOD_DAYS   = 30   # monthly draw cadence

# --- Default user-profile tier mix (Component-2 tiers) ---
# tickets_per_month is calibrated from C6 §3.2: Scout (1.0×) ≈ 2,650/mo,
# Legend (3.0×) ≈ 6,000–7,500/mo. Intermediate tiers interpolated.
# ALIGNED to the six Component-2 tiers (July 2026). The previous four-profile
# ladder (Scout / Explorer / Pathfinder / Legend at 1.0–3.0×) matched neither
# C2 nor C6 — "Explorer" and "Pathfinder" are not C2 tier names at all
# (Explorer is a per-track L2 badge, the likely source of the collision).
# Raffle multipliers below are the C2 §5.2 values, confirmed canonical against
# C6 §3.2, which cites Scout 1.0× and Legend 3.0× as its calibration endpoints.
# Tickets/mo = C6 §3.2 Scout baseline (2,650) × the tier's raffle multiplier,
# with Legend held at the top of C6's stated 6,000–7,500 band.
RAFFLE_DEFAULT_PROFILES = [
    {"Profile": "Scout (1.0×)",     "% of users": 40.0, "Tickets/mo": 2650},
    {"Profile": "Taster (1.2×)",    "% of users": 25.0, "Tickets/mo": 3180},
    {"Profile": "Collector (1.5×)", "% of users": 18.0, "Tickets/mo": 3975},
    {"Profile": "Analyst (2.0×)",   "% of users": 10.0, "Tickets/mo": 5300},
    {"Profile": "Expert (2.5×)",    "% of users":  5.0, "Tickets/mo": 6625},
    {"Profile": "Legend (3.0×)",    "% of users":  2.0, "Tickets/mo": 7500},
]

# Per-tier seed for the monthly long-format grid. Higher tiers are assumed to
# commit at higher rates (more engaged). All values are user-editable per month.
RAFFLE_TIER_NAMES = [
    "Scout (1.0×)", "Taster (1.2×)", "Collector (1.5×)",
    "Analyst (2.0×)", "Expert (2.5×)", "Legend (3.0×)",
]
RAFFLE_TIER_SEED = [
    {"Tier": "Scout (1.0×)",     "Profile (%)": 40.0, "Tickets": 2650, "Commit Rate (%)": 45.0},
    {"Tier": "Taster (1.2×)",    "Profile (%)": 25.0, "Tickets": 3180, "Commit Rate (%)": 55.0},
    {"Tier": "Collector (1.5×)", "Profile (%)": 18.0, "Tickets": 3975, "Commit Rate (%)": 62.0},
    {"Tier": "Analyst (2.0×)",   "Profile (%)": 10.0, "Tickets": 5300, "Commit Rate (%)": 72.0},
    {"Tier": "Expert (2.5×)",    "Profile (%)":  5.0, "Tickets": 6625, "Commit Rate (%)": 80.0},
    {"Tier": "Legend (3.0×)",    "Profile (%)":  2.0, "Tickets": 7500, "Commit Rate (%)": 85.0},
]


def fmt_ava(v: float, decimals_k: int = 1, decimals_m: int = 2) -> str:
    """
    Format a raw $AVA token amount with K (thousand) / M (million) units.
    Only uses M above 1,000,000; K between 1,000 and 1,000,000; raw below 1,000.
        850        -> '850'
        14_896     -> '14.9K'
        200_000    -> '200.0K'
        1_250_000  -> '1.25M'
    """
    v = float(v)
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1e6:.{decimals_m}f}M"
    if a >= 1_000:
        return f"{v / 1e3:.{decimals_k}f}K"
    return f"{v:,.0f}"


def pity_eligible_fraction(p_win: float, threshold: int = RAFFLE_PITY_THRESHOLD) -> float:
    """
    Stationary fraction of committers sitting at the pity threshold (C6 §8.2).

    Markov chain on the consecutive-loss counter 0…T, where reaching T absorbs
    back into 0 via the guaranteed win:

        π_T = q^T / Σ(k=0..T) q^k ,    q = 1 − p_win

    Generalised over the threshold T (was hardcoded to 3; C6 revised it to 2 in
    July 2026, so this now scales with RAFFLE_PITY_THRESHOLD). A lower threshold
    puts MORE users in the pity state — the guarantee fires more often, which
    raises the Consolation-tier load.
    """
    q = max(0.0, min(1.0, 1.0 - p_win))
    T = max(1, int(threshold))
    denom = sum(q ** k for k in range(T + 1))
    return (q ** T) / denom if denom > 0 else 0.0


def compute_raffle_draw(
    n_active: float,
    total_tickets_T: float,
    raffle_pool_tokens: float,
    mid_floor: int = RAFFLE_MID_FLOOR,
    mid_ceil: int = RAFFLE_MID_CEIL,
    cons_floor: int = RAFFLE_CONS_FLOOR,
    cons_ceil: int = RAFFLE_CONS_CEIL,
) -> dict:
    """
    Resolve one monthly draw from its aggregate inputs: the number of committers
    (n_active) and the total committed ticket pool (T). The per-tier user mix,
    tickets and commit rates are aggregated upstream in run_raffle_simulation.

    Winner floor/ceiling per tier are user-parametric (defaults from the C6 spec).
    Returns a flat dict of all quantities (one simulation row).
    """
    n_active = max(0.0, float(n_active))                            # committers
    total_tickets_T = max(0.0, float(total_tickets_T))             # T
    avg_tickets_per_committer = total_tickets_T / n_active if n_active > 0 else 0.0

    # ---- Tier budgets (tokens) ----
    jackpot_budget = raffle_pool_tokens * RAFFLE_JACKPOT_PCT / 100.0
    mid_budget     = raffle_pool_tokens * RAFFLE_MID_PCT     / 100.0
    cons_budget    = raffle_pool_tokens * RAFFLE_CONS_PCT    / 100.0
    buffer_budget  = raffle_pool_tokens * RAFFLE_BUFFER_PCT  / 100.0

    excess = 0.0

    # ---- Jackpot: 1 winner, fixed 200K, surplus → excess ----
    jackpot_winners  = 1
    jackpot_perprize = min(jackpot_budget, float(RAFFLE_JACKPOT_FIXED))
    excess += max(0.0, jackpot_budget - jackpot_perprize)

    # ---- Mid tier ----
    mid_winners = int(min(max(round(RAFFLE_MID_COEF * n_active), mid_floor), mid_ceil))
    mid_perprize = mid_budget / max(mid_winners, 1)
    if mid_perprize > RAFFLE_MID_PP_CEIL:
        excess += (mid_perprize - RAFFLE_MID_PP_CEIL) * mid_winners
        mid_perprize = float(RAFFLE_MID_PP_CEIL)
    elif mid_perprize < RAFFLE_MID_PP_FLOOR:
        mid_perprize = float(RAFFLE_MID_PP_FLOOR)
        mid_winners = int(mid_budget // mid_perprize)

    # ---- Consolation tier (formula-only, before pity) ----
    cons_target = int(min(max(round(RAFFLE_CONS_COEF * n_active), cons_floor), cons_ceil))
    cons_winners_formula = cons_target
    cons_perprize_formula = cons_budget / max(cons_winners_formula, 1)
    if cons_perprize_formula > RAFFLE_CONS_PP_CEIL:
        cons_perprize_formula = float(RAFFLE_CONS_PP_CEIL)
    elif cons_perprize_formula < RAFFLE_CONS_PP_FLOOR:
        cons_perprize_formula = float(RAFFLE_CONS_PP_FLOOR)
        cons_winners_formula = int(cons_budget // cons_perprize_formula)

    # ---- Win probability for the AVERAGE committer (used for pity Markov) ----
    W = jackpot_winners + mid_winners + cons_winners_formula
    if total_tickets_T > 0:
        p_avg = 1.0 - (1.0 - min(W / total_tickets_T, 1.0)) ** avg_tickets_per_committer
    else:
        p_avg = 0.0

    # ---- Pity load (C6 §8): eligibles served first, priority placement ----
    pity_frac = pity_eligible_fraction(p_avg)
    n_pity = min(n_active * pity_frac, float(cons_ceil))

    # Consolation winners with pity: max(formula target, pity count), ceiling-bound.
    cons_winners_pity = int(min(max(cons_target, n_pity), cons_ceil))
    cons_perprize_pity = cons_budget / max(cons_winners_pity, 1)
    if cons_perprize_pity < RAFFLE_CONS_PP_FLOOR:
        cons_perprize_pity = float(RAFFLE_CONS_PP_FLOOR)
        cons_winners_pity = int(cons_budget // cons_perprize_pity)

    total_winners = jackpot_winners + mid_winners + cons_winners_pity
    awarded = jackpot_perprize + mid_winners * mid_perprize + cons_winners_pity * cons_perprize_pity

    return {
        "N Active":             int(n_active),
        "Avg Tickets/Committer": int(avg_tickets_per_committer),
        "Total Tickets T":      int(total_tickets_T),
        "W (winner slots)":     int(W),
        "Jackpot Winners":      jackpot_winners,
        "Jackpot Per-Prize":    round(jackpot_perprize, 0),
        "Mid Winners":          mid_winners,
        "Mid Per-Prize":        round(mid_perprize, 0),
        "Cons Winners (formula)": cons_winners_formula,
        "Cons Per-Prize (formula)": round(cons_perprize_formula, 0),
        "Cons Winners (pity)":  cons_winners_pity,
        "Cons Per-Prize (pity)": round(cons_perprize_pity, 0),
        "Pity Fraction":        round(pity_frac, 4),
        "Pity Eligible":        int(n_pity),
        "Avg Win Prob":         round(p_avg, 4),
        "Total Winners":        int(total_winners),
        "Awarded (M)":          round(awarded / 1e6, 4),
        "Excess (M)":           round((excess + buffer_budget) / 1e6, 4),
        "Pool (M)":             round(raffle_pool_tokens / 1e6, 4),
    }


def weighted_tickets_per_committer(profiles_df: pd.DataFrame) -> float:
    """Tier-mix-weighted average monthly tickets per committer."""
    pct = profiles_df["% of users"].clip(lower=0)
    total = pct.sum()
    if total <= 0:
        return 0.0
    return float((pct / total * profiles_df["Tickets/mo"]).sum())


def run_raffle_simulation(
    grid_df: pd.DataFrame,
    raffle_pool_tokens: float,
    mid_floor: int = RAFFLE_MID_FLOOR,
    mid_ceil: int = RAFFLE_MID_CEIL,
    cons_floor: int = RAFFLE_CONS_FLOOR,
    cons_ceil: int = RAFFLE_CONS_CEIL,
    model_expiry: bool = True,
    expiry_days: int = RAFFLE_TICKET_EXPIRY_DAYS,
    draw_period_days: int = RAFFLE_DRAW_PERIOD_DAYS,
) -> pd.DataFrame:
    """
    Build the month-by-month raffle simulation from the long-format grid.

    grid_df is one row per (Month × Tier) with columns:
        Month, N Users, Tier, Profile (%), Tickets, Commit Rate (%).

    For each month, per-tier inputs are aggregated:
        tier_users      = N_users × Profile% / 100        (Profile% normalised to sum 100)
        tier_committers = tier_users × CommitRate% / 100
        N_active        = Σ tier_committers

    Ticket balance model (C6 §4) — model_expiry=True
    ------------------------------------------------
    Tickets are NOT use-it-or-lose-it within the month. Uncommitted tickets stay
    in the user's balance and expire only after `expiry_days`, rolling FIFO
    (oldest committed first). Each tier therefore carries a balance split into
    monthly vintages:

        survive_periods = round(expiry_days / draw_period_days)     # 60/30 → 2

      1. Earn:    this month's tickets are added as the newest vintage.
      2. Commit:  committers draw from the balance oldest-first. Aggregated as
                  committed = commit_rate × total_balance (balance assumed
                  evenly spread across the tier's users).
      3. Expire:  vintages older than survive_periods are burned.

    T (the committed ticket pool) is therefore LARGER than one month's earnings
    once carry-over builds up — which lowers each individual's win probability
    even though the prize budget is unchanged.

    model_expiry=False reproduces the legacy behaviour, where each month is
    independent and T = Σ committers × tier_tickets.
    """
    survive_periods = max(1, int(round(expiry_days / max(draw_period_days, 1))))

    rows = []
    tier_balances: dict[str, list] = {}    # tier name → vintages, index 0 = newest

    for month, g in grid_df.groupby("Month", sort=True):
        n_users = float(g["N Users"].iloc[0])                       # monthly total (first row)

        pct = g["Profile (%)"].clip(lower=0).astype(float)
        pct_sum = pct.sum()
        shares = (pct / pct_sum) if pct_sum > 0 else pct * 0.0      # normalise to 100%

        n_active = 0.0
        total_T = 0.0
        earned_total = 0.0
        expired_total = 0.0
        carried_total = 0.0

        for (_, r), share in zip(g.iterrows(), shares):
            tier_name       = str(r["Tier"])
            tier_users      = n_users * float(share)
            commit_frac     = float(r["Commit Rate (%)"]) / 100.0
            tier_committers = tier_users * commit_frac
            n_active += tier_committers

            earned = tier_users * float(r["Tickets"])
            earned_total += earned

            if not model_expiry:
                total_T += tier_committers * float(r["Tickets"])
                continue

            bal = tier_balances.setdefault(tier_name, [])
            bal.insert(0, earned)                       # newest vintage first

            # --- commit, oldest-first (FIFO) ---
            committed = sum(bal) * commit_frac
            remaining = committed
            i = len(bal) - 1
            while remaining > 1e-9 and i >= 0:
                take = min(bal[i], remaining)
                bal[i] -= take
                remaining -= take
                i -= 1
            total_T += committed

            # --- expire vintages beyond the window ---
            if len(bal) > survive_periods:
                expired_total += sum(bal[survive_periods:])
                del bal[survive_periods:]
            carried_total += sum(bal)

        rec = compute_raffle_draw(
            n_active=n_active, total_tickets_T=total_T,
            raffle_pool_tokens=raffle_pool_tokens,
            mid_floor=mid_floor, mid_ceil=mid_ceil,
            cons_floor=cons_floor, cons_ceil=cons_ceil,
        )
        rec["Month"]            = int(month)
        rec["N Users"]          = int(n_users)
        rec["Tickets Earned"]   = int(earned_total)
        rec["Tickets Expired"]  = int(expired_total)
        rec["Balance Carried"]  = int(carried_total)
        rows.append(rec)

    return pd.DataFrame(rows).sort_values("Month").reset_index(drop=True)


# ---- Raffle chart functions -------------------------------------------------

def fig_raffle_tokens(rdf: pd.DataFrame, raffle_pct: float) -> go.Figure:
    """Monthly raffle tokens (raw $AVA): awarded to winners by tier vs excess/rollover."""
    jackpot = rdf["Jackpot Winners"]     * rdf["Jackpot Per-Prize"]
    mid     = rdf["Mid Winners"]         * rdf["Mid Per-Prize"]
    cons    = rdf["Cons Winners (pity)"] * rdf["Cons Per-Prize (pity)"]
    excess  = rdf["Excess (M)"] * 1e6
    pool    = rdf["Pool (M)"]   * 1e6

    def hov(series, label):
        return [f"{label}: {fmt_ava(v)} $AVA" for v in series]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=rdf["Month"], y=jackpot, name="Jackpot", marker_color=COLORS["raffle"],
                         customdata=hov(jackpot, "Jackpot"), hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(x=rdf["Month"], y=mid, name="Mid", marker_color=COLORS["mms"],
                         customdata=hov(mid, "Mid"), hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(x=rdf["Month"], y=cons, name="Consolation", marker_color=COLORS["quests"],
                         customdata=hov(cons, "Consolation"), hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(x=rdf["Month"], y=excess, name="Excess / buffer → rollover",
                         marker_color=COLORS["neutral"], opacity=0.6,
                         customdata=hov(excess, "Excess"), hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(x=rdf["Month"], y=pool, mode="lines",
                             name=f"Total pool ({raffle_pct:.0f}% of MVT)",
                             line=dict(color=COLORS["floor"], dash="dash", width=2),
                             customdata=hov(pool, "Pool"), hovertemplate="%{customdata}<extra></extra>"))
    fig.update_layout(
        barmode="stack",
        title=f"Monthly Raffle Tokens Distributed by Tier  (pool = {raffle_pct:.0f}% of MVT, set in sidebar)",
        xaxis_title="Month", yaxis_title="$AVA",
        yaxis=dict(tickformat="~s"),
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=90, l=65, r=20), height=420,
    )
    return fig


def fig_prize_tiers(rdf: pd.DataFrame) -> go.Figure:
    """Winners (bars, left) and per-prize $AVA (lines, right) vs N_active over time."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(x=rdf["Month"], y=rdf["Mid Winners"], name="Mid winners",
                         marker_color=COLORS["mms"], opacity=0.7,
                         hovertemplate="Mid winners: %{y}<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Bar(x=rdf["Month"], y=rdf["Cons Winners (pity)"], name="Consolation winners",
                         marker_color=COLORS["quests"], opacity=0.7,
                         hovertemplate="Cons winners: %{y}<extra></extra>"), secondary_y=False)

    fig.add_trace(go.Scatter(x=rdf["Month"], y=rdf["Mid Per-Prize"], mode="lines+markers",
                             name="Mid per-prize", line=dict(color=COLORS["mms"], width=2.5),
                             customdata=[f"Mid per-prize: {fmt_ava(v)} $AVA" for v in rdf["Mid Per-Prize"]],
                             hovertemplate="%{customdata}<extra></extra>"), secondary_y=True)
    fig.add_trace(go.Scatter(x=rdf["Month"], y=rdf["Cons Per-Prize (pity)"], mode="lines+markers",
                             name="Cons per-prize", line=dict(color=COLORS["quests"], width=2.5),
                             customdata=[f"Cons per-prize: {fmt_ava(v)} $AVA" for v in rdf["Cons Per-Prize (pity)"]],
                             hovertemplate="%{customdata}<extra></extra>"), secondary_y=True)

    fig.add_hline(y=RAFFLE_MID_PP_FLOOR, line_dash="dot", line_color=COLORS["mms"], opacity=0.4,
                  annotation_text="Mid floor 600", annotation_position="top left", secondary_y=True)
    fig.add_hline(y=RAFFLE_CONS_PP_FLOOR, line_dash="dot", line_color=COLORS["quests"], opacity=0.4,
                  annotation_text="Cons floor 80", annotation_position="bottom left", secondary_y=True)

    fig.update_yaxes(title_text="Winners per draw", secondary_y=False)
    fig.update_yaxes(title_text="Per-prize $AVA", tickformat="~s", secondary_y=True)
    fig.update_layout(
        barmode="group",
        title="Prize Tier Structure vs Participation  (winners scale with N_active; per-prize dilutes)",
        xaxis_title="Month", legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=90, l=65, r=75), height=420,
    )
    return fig


def fig_tickets_farmed_committed(rdf: pd.DataFrame) -> go.Figure:
    """Total committed ticket pool T over time, with N_active overlay."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=rdf["Month"], y=rdf["Total Tickets T"] / 1e6,
                         name="Committed tickets T (millions)", marker_color=COLORS["scan"],
                         hovertemplate="T: %{y:.2f}M tickets<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=rdf["Month"], y=rdf["N Active"], mode="lines+markers",
                             name="N_active (committers)", line=dict(color=COLORS["contributor"], width=2.5),
                             hovertemplate="N_active: %{y:,}<extra></extra>"), secondary_y=True)
    fig.update_yaxes(title_text="Committed tickets (millions)", secondary_y=False)
    fig.update_yaxes(title_text="Active committers", secondary_y=True)
    fig.update_layout(
        title="Committed Ticket Pool (T) and Active Committers",
        xaxis_title="Month", legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=80, l=65, r=75), height=380,
    )
    return fig


def fig_win_prob_curve(rdf: pd.DataFrame, ref_month: int = None) -> go.Figure:
    """P(win ≥1) and per-tier odds vs committed tickets x, for the chosen month's pool."""
    if ref_month is not None and (rdf["Month"] == ref_month).any():
        last = rdf[rdf["Month"] == ref_month].iloc[0]
    else:
        last = rdf.iloc[-1]
    T = max(last["Total Tickets T"], 1)
    W = last["W (winner slots)"]
    mid_w  = last["Mid Winners"]
    cons_w = last["Cons Winners (pity)"]

    x = np.linspace(0, 50_000, 200)
    p_any  = 1.0 - (1.0 - min(W / T, 1.0)) ** x
    p_jack = 1.0 - (1.0 - min(1 / T, 1.0)) ** x
    p_mid  = 1.0 - (1.0 - min(mid_w / T, 1.0)) ** x
    p_cons = 1.0 - (1.0 - min(cons_w / T, 1.0)) ** x

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=p_any * 100, mode="lines", name="P(win ≥1 any)",
                             line=dict(color=COLORS["raffle"], width=3),
                             hovertemplate="%{x:,.0f} tickets → %{y:.1f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=p_mid * 100, mode="lines", name="P(win Mid)",
                             line=dict(color=COLORS["mms"], width=2)))
    fig.add_trace(go.Scatter(x=x, y=p_cons * 100, mode="lines", name="P(win Consolation)",
                             line=dict(color=COLORS["quests"], width=2)))
    fig.add_trace(go.Scatter(x=x, y=p_jack * 100, mode="lines", name="P(win Jackpot)",
                             line=dict(color=COLORS["floor"], width=2, dash="dash")))

    fig.add_vline(x=last["Avg Tickets/Committer"], line_dash="dot", line_color=COLORS["neutral"],
                  annotation_text=f"avg committer ≈ {int(last['Avg Tickets/Committer']):,}",
                  annotation_position="top right")
    fig.update_layout(
        title=(f"Win Probability vs Committed Tickets  "
               f"(Month {int(last['Month'])}: N_active={int(last['N Active']):,}, "
               f"T={fmt_ava(last['Total Tickets T'])} tickets, W={int(last['W (winner slots)']):,} slots)"),
        xaxis_title="Tickets committed by a user (x)", yaxis_title="Probability (%)",
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=80, l=65, r=20), height=400,
    )
    return fig


def fig_pity_population(rdf: pd.DataFrame) -> go.Figure:
    """Pity-eligible committers and the resulting Consolation per-prize dilution."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=rdf["Month"], y=rdf["Pity Eligible"], name="Pity-eligible committers",
                         marker_color=COLORS["floor"], opacity=0.65,
                         hovertemplate="Pity-eligible: %{y:,}<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=rdf["Month"], y=rdf["Pity Fraction"] * 100, mode="lines+markers",
                             name="π₃ (% of committers)", line=dict(color=COLORS["neutral"], width=2),
                             hovertemplate="π₃: %{y:.1f}%<extra></extra>"), secondary_y=True)
    fig.add_trace(go.Scatter(x=rdf["Month"], y=rdf["Cons Per-Prize (formula)"], mode="lines",
                             name="Cons per-prize (no pity)", line=dict(color=COLORS["quests"], dash="dot", width=2),
                             customdata=[f"No pity: {fmt_ava(v)} $AVA" for v in rdf["Cons Per-Prize (formula)"]],
                             hovertemplate="%{customdata}<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=rdf["Month"], y=rdf["Cons Per-Prize (pity)"], mode="lines",
                             name="Cons per-prize (with pity)", line=dict(color=COLORS["quests"], width=2.5),
                             customdata=[f"With pity: {fmt_ava(v)} $AVA" for v in rdf["Cons Per-Prize (pity)"]],
                             hovertemplate="%{customdata}<extra></extra>"), secondary_y=False)
    fig.add_hline(y=RAFFLE_CONS_PP_FLOOR, line_dash="dash", line_color=COLORS["floor"], opacity=0.5,
                  annotation_text="80 $AVA floor", annotation_position="bottom right", secondary_y=False)
    fig.update_yaxes(title_text="Committers / per-prize $AVA", secondary_y=False)
    fig.update_yaxes(title_text="π₃ (% of committers)", secondary_y=True)
    fig.update_layout(
        title="Pity Load: Eligible Population (Markov π₃) and Consolation Per-Prize Dilution",
        xaxis_title="Month", legend=dict(orientation="h", y=-0.3),
        margin=dict(t=55, b=100, l=65, r=75), height=420,
    )
    return fig


def fig_expected_winnings(rdf: pd.DataFrame, profiles_df: pd.DataFrame, ref_month: int = None) -> go.Figure:
    """Expected annual $AVA winnings per profile (chosen month's pool), stacked by tier."""
    if ref_month is not None and (rdf["Month"] == ref_month).any():
        last = rdf[rdf["Month"] == ref_month].iloc[0]
    else:
        last = rdf.iloc[-1]
    T = max(last["Total Tickets T"], 1)
    mid_w, cons_w = last["Mid Winners"], last["Cons Winners (pity)"]
    jp, mp, cp = last["Jackpot Per-Prize"], last["Mid Per-Prize"], last["Cons Per-Prize (pity)"]

    names, ev_j, ev_m, ev_c = [], [], [], []
    for _, pr in profiles_df.iterrows():
        x = pr["Tickets/mo"]
        names.append(pr["Profile"])
        ev_j.append(min(x / T, 1.0) * jp * 12)
        ev_m.append(min(x * mid_w / T, 1.0) * mp * 12)
        ev_c.append(min(x * cons_w / T, 1.0) * cp * 12)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=ev_j, name="Jackpot EV", marker_color=COLORS["raffle"],
                         customdata=[f"Jackpot EV: {fmt_ava(v)} $AVA/yr" for v in ev_j],
                         hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(x=names, y=ev_m, name="Mid EV", marker_color=COLORS["mms"],
                         customdata=[f"Mid EV: {fmt_ava(v)} $AVA/yr" for v in ev_m],
                         hovertemplate="%{customdata}<extra></extra>"))
    fig.add_trace(go.Bar(x=names, y=ev_c, name="Consolation EV", marker_color=COLORS["quests"],
                         customdata=[f"Cons EV: {fmt_ava(v)} $AVA/yr" for v in ev_c],
                         hovertemplate="%{customdata}<extra></extra>"))
    fig.update_layout(
        barmode="stack",
        title=f"Expected Annual Raffle Winnings by User Profile  (Month {int(last['Month'])} pool, commits all monthly tickets)",
        xaxis_title="User profile (Component-2 tier)", yaxis_title="Expected $AVA / year",
        yaxis=dict(tickformat="~s"),
        legend=dict(orientation="h", y=-0.25),
        margin=dict(t=55, b=90, l=65, r=20), height=400,
    )
    return fig


# ==============================================================================
# 5. SIDEBAR — HYPERPARAMETER INPUTS
# ==============================================================================

def render_sidebar() -> TokenomicsParams:
    """
    Renders all sidebar controls and returns a fully populated TokenomicsParams.
    To add a new hyperparameter: add a widget here and a field to TokenomicsParams.
    """
    st.sidebar.title("⚙️ Hyperparameters")
    st.sidebar.caption("Every chart updates in real time as you adjust these values.")

    # ---- Token Supply ----------------------------------------
    st.sidebar.header("🪙 Token Supply")

    total_supply_m = st.sidebar.number_input(
        "Total token supply (millions)", min_value=100.0, max_value=20_000.0,
        value=2560.0, step=10.0,
        help="Total $AVA at genesis. Default: 2.56 billion.",
    )
    community_pool_pct = st.sidebar.slider(
        "Community pool (% of total supply)",
        min_value=10, max_value=60, value=39,
        help="Share of total supply reserved for contributor rewards. Default: 39%.",
    )
    tge_unlock_pct = st.sidebar.slider(
        "TGE unlock (% of community pool → Reserve Cushion)",
        min_value=5, max_value=40, value=20,
        help="% of community pool released at Token Generation Event as the RC buffer.",
    )
    vesting_months = st.sidebar.slider(
        "Vesting duration (months)", min_value=12, max_value=120, value=60,
        help="Linear monthly vesting of remaining community pool after TGE.",
    )

    # ---- Budget Safety -----------------------------------------------
    st.sidebar.header("🛡️ Budget Safety")

    mms_multiplier = st.sidebar.slider(
        "MMS multiplier (×MVT)",
        min_value=1.0, max_value=5.0, value=2.0, step=0.1,
        help=(
            "Maximum Monthly Spend = MVT × this value. "
            "Soft cap fires if a month's total accruals exceed MMS."
        ),
    )

    # ---- Category Splits ----------------------------------------
    st.sidebar.header("📊 Category Budget Splits")
    st.sidebar.caption("Quests % is auto-calculated as the remainder.")

    scan_pct = st.sidebar.slider("Scan-to-Earn (%)", 50, 92, 80)
    remaining = 100 - scan_pct

    raffle_pct = st.sidebar.slider(
        "Raffle (%)", 1, max(1, remaining - 2), min(7, max(1, remaining - 2)),
    )
    remaining2 = remaining - raffle_pct

    contributor_pct = st.sidebar.slider(
        "Contributor Tracks (%)", 1, max(1, remaining2 - 1),
        min(7, max(1, remaining2 - 1)),
    )
    quests_pct = remaining2 - contributor_pct  # auto-remainder

    total_check = scan_pct + raffle_pct + contributor_pct + quests_pct
    if total_check == 100:
        st.sidebar.success(f"✓ Total = 100%  (Quests = {quests_pct}%)")
    else:
        st.sidebar.info(f"ℹ Quests auto-set to {quests_pct}%  (Total = {total_check}%)")

    # ---- Volume Assumptions -------------------------------------
    st.sidebar.header("📈 Volume (Steady State)")

    new_scans_monthly = st.sidebar.number_input(
        "New scans / month", min_value=100, max_value=5_000_000,
        value=10_000, step=1_000,
        help="Unique product scans per month at steady state.",
    )
    dup_scans_monthly = st.sidebar.number_input(
        "Duplicate scans / month", min_value=1_000, max_value=50_000_000,
        value=100_000, step=10_000,
        help="Re-scans of already-catalogued products per month.",
    )
    dup_divisor = st.sidebar.slider(
        "Duplicate rate divisor (D)", min_value=5.0, max_value=50.0,
        value=20.0, step=1.0,
        help="Duplicate scan earns 1/D of a new scan reward.",
    )

    st.sidebar.subheader("Duplicate-scan model")
    dup_model = st.sidebar.radio(
        "New-vs-duplicate split",
        options=["coverage", "static"],
        index=0,
        format_func=lambda v: (
            "Coverage-based (Cₜ/N coupon-collector)" if v == "coverage"
            else "Static ratio (legacy)"
        ),
        help="Coverage-based: the two volume inputs above define TOTAL scan "
             "demand; the new/dup split emerges from catalogue coverage — new "
             "scans start high and taper as the catalogue fills, duplicates "
             "rise toward saturation. Static: both inputs are used as-is and "
             "scale together with the launch curve (legacy behaviour).",
    )
    item_universe_n = st.sidebar.number_input(
        "Item universe N", min_value=10_000, max_value=50_000_000,
        value=500_000, step=50_000,
        help="Estimated total addressable item space (e.g. retail SKU count "
             "for the target market). Rough proxy is fine — coverage dynamics "
             "matter more than the exact figure.",
        disabled=(dup_model != "coverage"),
    )
    initial_catalog_items = st.sidebar.number_input(
        "Initial catalogue size C₀", min_value=0, max_value=10_000_000,
        value=30_000, step=5_000,
        help="Items already catalogued at mainnet launch. Default ≈ testnet "
             "seeding: 1,000 testers × 5 new scans/month × 6 months.",
        disabled=(dup_model != "coverage"),
    )
    popularity_weight = st.sidebar.slider(
        "Popularity weight w", min_value=1.0, max_value=20.0, value=1.0, step=0.5,
        help="Scan concentration on popular items (Zipf proxy). Discovery per "
             "month = (N−Cₜ)·(1−exp(−S/(w·N))): higher w shrinks effective "
             "discovery breadth, raising the duplicate rate at every coverage "
             "level. w = 1 is the pure uniform Cₜ/N model.",
        disabled=(dup_model != "coverage"),
    )

    # ---- Price & Floor ------------------------------------------
    # ----------------------------------------------------------------------
    st.sidebar.header("🎚️ Component 2 Tier Multipliers")
    st.sidebar.caption(
        "C2 grants higher-level contributors a 1.00×–2.00× scan multiplier. "
        "The hard cap binds LAST (on the final paid amount), so no tier can "
        "ever be paid above it."
    )

    enable_tier_multipliers = st.sidebar.checkbox(
        "Apply C2 scan multipliers", value=True,
        help="Off = legacy flat-rate pricing (every scan costs the same "
             "regardless of contributor level).",
    )

    tier_ramp_months = st.sidebar.slider(
        "Tier ramp (months)", min_value=0, max_value=36, value=12,
        help="Months for the population to migrate from all-Scout at launch to "
             "the steady mix below. Tiers only ratchet upward (XP never "
             "decays), so the blended multiplier — and the cost per scan — "
             "drifts up over time. 0 = start at the steady mix immediately.",
        disabled=not enable_tier_multipliers,
    )

    tier_df = pd.DataFrame({
        "Tier":        C2_TIER_NAMES,
        "Scan mult":   C2_SCAN_MULT,
        "% of users":  list(C2_DEFAULT_MIX),
        "Scans/user":  list(C2_DEFAULT_INTENSITY),
    })
    edited_tiers = st.sidebar.data_editor(
        tier_df,
        hide_index=True,
        disabled=["Tier", "Scan mult"] if enable_tier_multipliers
                 else ["Tier", "Scan mult", "% of users", "Scans/user"],
        column_config={
            "Scan mult": st.column_config.NumberColumn(
                format="%.2f×",
                help="Locked to the C2 spec.",
            ),
            "% of users": st.column_config.NumberColumn(
                format="%.1f%%", min_value=0.0, max_value=100.0,
                help="Steady-state population share of this tier.",
            ),
            "Scans/user": st.column_config.NumberColumn(
                format="%.1f", min_value=0.0,
                help="Relative scans per user per month. Cost is VOLUME-"
                     "weighted, so high tiers count more than their headcount.",
            ),
        },
        key="c2_tier_editor",
    )
    tier_mix_pct        = tuple(float(v) for v in edited_tiers["% of users"])
    tier_scan_intensity = tuple(float(v) for v in edited_tiers["Scans/user"])

    if enable_tier_multipliers:
        _mix_total = sum(tier_mix_pct)
        if abs(_mix_total - 100.0) > 0.5:
            st.sidebar.warning(
                f"⚠️ Tier mix sums to {_mix_total:.1f}% — shares are "
                "renormalised, but check this is intended."
            )

    # ----------------------------------------------------------------------
    st.sidebar.header("💶 Price & Floor")

    token_price_eur = st.sidebar.number_input(
        "Token price at ICO (EUR)", min_value=0.0001, max_value=10.0,
        value=0.0098, step=0.0001, format="%.4f",
        help="Used only for EUR-equivalent displays. Does not affect token logic.",
    )
    floor_new_scan = st.sidebar.number_input(
        "Floor: $AVA per new scan (minimum guarantee)",
        min_value=1, max_value=100_000, value=70, step=10,
        help="Hard floor: new scan always earns at least this many $AVA.",
    )
    hard_cap_new_scan = st.sidebar.number_input(
        "Hard cap: $AVA per new scan (maximum)",
        min_value=1, max_value=100_000, value=250, step=10,
        help=(
            "No single new scan earns more than this many $AVA, regardless of the "
            "pool-derived rate. Unused scan budget due to the cap flows directly into the "
            "Reserve Cushion (RC) each month. Must be ≥ floor."
        ),
    )
    if hard_cap_new_scan < floor_new_scan:
        st.sidebar.error("⚠️ Hard cap must be ≥ floor. Adjust one of the two values.")

    # ---- Testnet ------------------------------------------------
    st.sidebar.header("🧪 Testnet")

    testnet_alpha = st.sidebar.slider(
        "Testnet multiplier (α)", min_value=1.0, max_value=20.0,
        value=5.0, step=0.5,
        help="All mainnet base rates × α for testnet contributors.",
    )
    testnet_pool_m = st.sidebar.number_input(
        "Testnet pool (millions)", min_value=10.0, max_value=500.0,
        value=128.0, step=10.0,
    )
    testnet_tge_pct = st.sidebar.slider(
        "Testnet TGE unlock (%)", min_value=0, max_value=100, value=25,
        help="Per the allocation sheet: % of the testnet pool unlocked immediately "
             "at TGE. The remainder vests linearly over the testnet window.",
    )
    testnet_months = st.sidebar.slider(
        "Testnet duration (months)", min_value=1, max_value=18, value=6,
    )
    testnet_chart_users = st.sidebar.number_input(
        "RC chart: active testnet users",
        min_value=10, max_value=50_000, value=1_000, step=100,
        help=(
            "Number of active testers assumed for the combined RC timeline chart in the "
            "Simulation tab. Each tester is modelled as 5 new scans + 20 dup scans per month "
            "at testnet rates (α-scaled). Adjust to stress-test pool runway."
        ),
    )

    # ---- Simulation / Launch Volume Curve -----------------------
    st.sidebar.header("🚀 Simulation: Launch Volume Curve")

    spike_multiplier = st.sidebar.slider(
        "Launch spike multiplier", min_value=1.0, max_value=50.0,
        value=5.0, step=0.5,
        help="Peak volume = steady-state × this multiplier.",
    )
    spike_months = st.sidebar.slider(
        "Spike duration (months)", min_value=1, max_value=6, value=2,
        help="How many months the full spike lasts.",
    )
    decay_months = st.sidebar.slider(
        "Post-spike decay (months)", min_value=1, max_value=18, value=4,
        help="Months to linearly decay from spike back to steady state.",
    )

    return TokenomicsParams(
        total_supply_m      = total_supply_m,
        community_pool_pct  = community_pool_pct,
        tge_unlock_pct      = tge_unlock_pct,
        vesting_months      = vesting_months,
        mms_multiplier      = mms_multiplier,
        scan_pct            = scan_pct,
        raffle_pct          = raffle_pct,
        contributor_pct     = contributor_pct,
        quests_pct          = quests_pct,
        new_scans_monthly   = new_scans_monthly,
        dup_scans_monthly   = dup_scans_monthly,
        dup_divisor         = dup_divisor,
        enable_tier_multipliers = enable_tier_multipliers,
        tier_mix_pct            = tier_mix_pct,
        tier_scan_intensity     = tier_scan_intensity,
        tier_ramp_months        = int(tier_ramp_months),
        dup_model           = dup_model,
        item_universe_n     = int(item_universe_n),
        initial_catalog_items = int(initial_catalog_items),
        popularity_weight   = float(popularity_weight),
        token_price_eur     = token_price_eur,
        floor_new_scan      = floor_new_scan,
        hard_cap_new_scan   = hard_cap_new_scan,
        testnet_alpha       = testnet_alpha,
        testnet_pool_m      = testnet_pool_m,
        testnet_months      = testnet_months,
        testnet_tge_pct     = float(testnet_tge_pct),
        testnet_chart_users = testnet_chart_users,
        spike_multiplier    = spike_multiplier,
        spike_months        = spike_months,
        decay_months        = decay_months,
    )


# ==============================================================================
# 6. MAIN APP
# ==============================================================================

def main():

    # ---- Page header ----
    st.title("🌿 Aveniva — Component 1: Token Distribution Model")
    st.markdown(
        "**How this model works — the two-layer structure**\n\n"
        "The community pool of **~998M \\$AVA** (39% of total supply) is the single source of "
        "all contributor rewards. It splits into two budget mechanisms:\n\n"
        "- **Monthly Vesting Tranche (MVT):** ~13.3M \\$AVA/month from the 60-month linear "
        "vesting schedule — the steady-state operating budget for all reward categories.\n"
        "- **Reserve Cushion (RC):** ~199.7M \\$AVA unlocked at TGE (20% of the community "
        "pool). Held as a launch-spike buffer — each month can draw up to **MMS = 2× MVT** "
        "from the combined MVT + RC. Unspent MVT in quiet months rolls back into the RC.\n\n"
        "The MVT is split across four reward buckets — Scan-to-Earn, Raffle, Contributor "
        "Tracks, and Quests — by the % sliders in the sidebar. Within the Scan-to-Earn "
        "bucket, per-scan rates are back-calculated: `rate = budget / (new_scans + dup_scans ÷ D)`.\n\n"
        "**Soft cap:** if a month's total accruals exceed MMS (e.g. a launch spike), a scaling "
        "factor compresses every reward proportionally. A hard **floor** guarantees every new "
        "scan earns at least the defined minimum regardless of volume. "
        "**Testnet** rates apply multiplier α to all mainnet base rates, funded from the "
        "separate 128M \\$AVA testnet pool. Adjust any parameter in the sidebar — all outputs "
        "update in real time."
    )
    st.markdown("---")

    # ---- Build parameter and metric objects ----
    params  = render_sidebar()
    metrics = derive_metrics(params)
    sim_df  = run_simulation(params, metrics, n_months=36)

    # ====================================================================
    # TAB LAYOUT
    # ====================================================================
    tab_overview, tab_sim, tab_raffle, tab_testnet = st.tabs([
        "📊 Overview & Rates",
        "📉 Simulation",
        "🎟️ Raffle",
        "🧪 Testnet",
    ])

    # ================================================================
    # TAB 1 — OVERVIEW
    # ================================================================
    with tab_overview:

        # ---- Top-level key metrics — Row 1: supply-side ----
        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.metric(
            "Community Pool",
            f"{metrics.community_pool_m:.0f}M $AVA",
            f"{params.community_pool_pct}% of total supply",
            help=(
                "Total $AVA allocated to all contributor rewards (scans, raffle, quests, "
                "contributor tracks). Set by the 'Community pool %' slider. "
                "Default: 998.4M $AVA (39% of 2.56B supply)."
            ),
        )
        r1c2.metric(
            "Reserve Cushion (RC)",
            f"{metrics.rc_m:.1f}M $AVA",
            f"≈ {metrics.rc_m / max(metrics.mvt_m, 0.001):.0f} months of MVT",
            help=(
                "Portion of the community pool unlocked at TGE and held as a launch-spike "
                "buffer. Each month the system can draw up to MMS = MVT × multiplier from "
                "the combined MVT + RC pot. Unspent MVT rolls back into RC in quiet months."
            ),
        )
        r1c3.metric(
            "Monthly Vesting (MVT)",
            f"{metrics.mvt_m:.2f}M $AVA / month",
            help=(
                "Monthly Vesting Tranche — the steady-state token budget released each month "
                "by the linear vesting schedule: (community pool − RC) ÷ vesting months. "
                "This is what funds all reward categories at steady state."
            ),
        )

        # ---- Row 2: reward-side ----
        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.metric(
            "Max Monthly Spend (MMS)",
            f"{metrics.mms_m:.2f}M $AVA / month",
            f"{params.mms_multiplier}× MVT",
            help=(
                "Maximum Monthly Spend = MVT × MMS multiplier. "
                "If a month's total accruals exceed MMS (e.g. during a launch spike), "
                "the soft cap fires and all payouts are scaled down proportionally "
                "by factor = MMS ÷ total_accruals."
            ),
        )
        r2c2.metric(
            "Effective new scan reward",
            f"{metrics.effective_new_scan_tokens:,.0f} $AVA",
            f"€{metrics.effective_new_scan_eur:.2f} at ICO price",
            help=(
                "Actual reward paid per new scan = min(pool-derived rate, hard cap). "
                "At default settings the hard cap (250 $AVA) binds because the pool rate "
                "(710 $AVA) exceeds it. Unused budget flows to RC each month."
            ),
        )
        r2c3.metric(
            "Hard cap / Floor",
            f"{params.hard_cap_new_scan:,} / {params.floor_new_scan:,} $AVA",
            f"€{params.hard_cap_new_scan * params.token_price_eur:.2f} / €{params.floor_new_scan * params.token_price_eur:.2f}",
            help=(
                "Hard cap: maximum $AVA per new scan — any scan budget above this is redirected to RC. "
                "Floor: minimum $AVA per new scan — honoured even when the soft cap fires at extreme volume. "
                "The effective rate at any point = max(min(pool_rate × scaling, hard_cap), floor)."
            ),
        )

        st.markdown("---")

        # ---- Bucket chart | Per-action rate table ----
        col_chart, col_table = st.columns([1, 1.7])

        with col_chart:
            st.plotly_chart(fig_bucket_donut(params, metrics), use_container_width=True)

            # Compact monthly budget summary beneath the donut
            st.markdown("**Monthly category budgets (steady state)**")
            budget_df = pd.DataFrame({
                "Category":        ["Scan-to-Earn", "Raffle", "Contributor", "Quests"],
                "% of MVT":        [f"{params.scan_pct}%", f"{params.raffle_pct}%",
                                    f"{params.contributor_pct}%", f"{params.quests_pct}%"],
                "M $AVA / mo":   [f"{metrics.scan_budget_m:.3f}",
                                    f"{metrics.raffle_budget_m:.3f}",
                                    f"{metrics.contributor_budget_m:.3f}",
                                    f"{metrics.quests_budget_m:.3f}"],
                "EUR / mo (ICO)":  [
                    f"€{metrics.scan_budget_m        * 1e6 * params.token_price_eur:,.0f}",
                    f"€{metrics.raffle_budget_m      * 1e6 * params.token_price_eur:,.0f}",
                    f"€{metrics.contributor_budget_m * 1e6 * params.token_price_eur:,.0f}",
                    f"€{metrics.quests_budget_m      * 1e6 * params.token_price_eur:,.0f}",
                ],
            })
            st.dataframe(budget_df, hide_index=True, use_container_width=True)

        with col_table:
            st.markdown("**Per-action reward rates at base volume**")
            rate_df = build_rate_table(params, metrics)
            st.dataframe(rate_df, hide_index=True, use_container_width=True, height=330)

            # Scan budget utilisation health check.
            # NOTE: uses EFFECTIVE (hard-capped) rates — what contributors are
            # actually paid — not the pool-derived base rate. The base rate is
            # back-calculated from the scan budget, so base-rate cost ≡ budget
            # (always exactly 100%), which made this check meaningless and let
            # floating-point noise flip it to a false "OVER budget" warning.
            # Uses BLENDED rates so C2 tier multipliers are reflected in the
            # budget check (supervisor feedback, C2 point 2).
            new_cost_m  = params.new_scans_monthly * metrics.blended_new_scan_tokens / 1e6
            dup_cost_m  = params.dup_scans_monthly  * metrics.blended_dup_tokens      / 1e6
            total_scan_m = new_cost_m + dup_cost_m
            utilisation  = total_scan_m / max(metrics.scan_budget_m, 0.001) * 100

            st.markdown("**Scan bucket utilisation at base volume**")
            u1, u2, u3 = st.columns(3)
            u1.metric(
                "New scan cost",
                f"{new_cost_m:.2f}M $AVA",
                help="Monthly $AVA spent on new-scan rewards at steady-state volume, "
                     "at the effective (hard-capped) rate contributors actually earn.",
            )
            u2.metric(
                "Duplicate cost",
                f"{dup_cost_m:.2f}M $AVA",
                help="Monthly $AVA spent on duplicate-scan rewards at steady-state volume.",
            )
            u3.metric(
                "Budget utilisation",
                f"{utilisation:.1f}%",
                delta=f"{'Under' if utilisation <= 100 else 'OVER'} budget",
                delta_color="normal" if utilisation <= 100 else "inverse",
            )
            if utilisation > 100:
                st.warning(
                    "⚠️ Base volume exceeds scan bucket — soft cap will fire every month. "
                    "Reduce volume assumptions or lower base scan rate."
                )
            elif utilisation < 50:
                st.info(
                    "ℹ️ Scan budget is less than 50% utilised at base volume. "
                    "The unspent balance rolls into RC each month."
                )

    # ================================================================
    # TAB 2 — SIMULATION
    # ================================================================
    with tab_sim:

        st.markdown(
            "36-month simulation using the launch volume curve from the sidebar. "
            "**Gold ✕ markers** = months where the soft cap fires. "
            "All amounts in millions of \\$AVA unless noted."
        )

        st.markdown("---")

        # Volume profile
        st.plotly_chart(fig_volume_profile(sim_df), use_container_width=True)

        # Coverage curve (only meaningful for the coverage-based dup model)
        if params.dup_model == "coverage":
            st.plotly_chart(fig_coverage_curve(sim_df, params), use_container_width=True)

        # C2 tier-multiplier blend over time
        st.plotly_chart(
            fig_tier_multiplier_curve(sim_df, params, metrics),
            use_container_width=True,
        )

        # Scan reward curve
        st.plotly_chart(
            fig_scan_rate_curve(sim_df, params, metrics),
            use_container_width=True,
        )

        # Monthly spend by category
        st.plotly_chart(fig_monthly_spend_stack(sim_df), use_container_width=True)

        # Combined RC balance: testnet → ICO → public
        st.plotly_chart(
            fig_rc_combined_timeline(params, metrics, sim_df),
            use_container_width=True,
        )

        # ---- Raw data table (collapsible) ----
        with st.expander("📋 Full simulation data table (all 36 months)"):
            display_cols = [
                "Month", "Vol Multiplier", "New Scans", "Dup Scans",
                "Total Accruals (M)", "Actual Spend (M)",
                "RC Balance (M)", "Soft Cap Fired", "Scaling Factor",
                "Eff New Scan Tokens", "Eff New Scan EUR",
            ]
            # Highlight soft-cap rows in amber
            def highlight_cap(row):
                color = "background-color: rgba(255,209,102,0.20)" if row["Soft Cap Fired"] else ""
                return [color] * len(row)

            styled = sim_df[display_cols].style.apply(highlight_cap, axis=1)
            st.dataframe(styled, hide_index=True, use_container_width=True)

    # ================================================================
    # TAB 3 — RAFFLE (Component 6)
    # ================================================================
    with tab_raffle:

        st.markdown(
            "**Component 6 — Raffle System.** Two coupled simulations: the **token "
            "distribution** (prize pool = the **Raffle %** you set in the sidebar's "
            "*Category Budget Splits* — currently **{pct:.0f}% of MVT ≈ {pool} \\$AVA/mo**, "
            "independent of scan volume) and the **ticket economy** (engagement-driven — "
            "scanning earns \\$AVA but **zero tickets**). Edit any month's scenario in the "
            "grid below; every chart updates live.".format(
                pct=params.raffle_pct, pool=fmt_ava(metrics.raffle_budget_m * 1e6))
        )

        raffle_pool_tokens = metrics.raffle_budget_m * 1e6

        # ---- Scenario seed controls ----
        c1, c2 = st.columns(2)
        n_months_raffle = c1.number_input(
            "Months to simulate", min_value=1, max_value=120, value=12, step=1,
            help="Number of monthly draws. Changing this rebuilds the grid below.",
        )
        default_users = c2.number_input(
            "Starting active users (per month)", min_value=100, max_value=5_000_000,
            value=10_000, step=1_000,
            help="Seeds the monthly total-users value for every month. Override any month in the grid.",
        )

        # ---- Prize-tier winner bounds (parametric floors & ceilings) ----
        st.markdown("**Prize-tier winner bounds** — winners scale with N_active "
                    "(Mid = 1% × N_active, Consolation = 15% × N_active), clamped to these limits.")
        wc1, wc2, wc3, wc4 = st.columns(4)
        mid_floor = wc1.number_input("Mid winners — floor", min_value=1, max_value=100_000,
                                     value=RAFFLE_MID_FLOOR, step=1)
        mid_ceil  = wc2.number_input("Mid winners — ceiling", min_value=int(mid_floor), max_value=1_000_000,
                                     value=max(RAFFLE_MID_CEIL, int(mid_floor)), step=10)
        cons_floor = wc3.number_input("Consolation winners — floor", min_value=1, max_value=100_000,
                                      value=RAFFLE_CONS_FLOOR, step=10)
        cons_ceil  = wc4.number_input("Consolation winners — ceiling", min_value=int(cons_floor), max_value=1_000_000,
                                      value=max(RAFFLE_CONS_CEIL, int(cons_floor)), step=100)

        # ---- Ticket lifecycle (C6 §4) ----
        st.markdown("**Ticket lifecycle**")
        ec1, ec2, ec3 = st.columns([1.4, 1, 1])
        model_expiry = ec1.checkbox(
            "Model ticket carry-over & expiry", value=True,
            help="On: uncommitted tickets stay in the user's balance and expire "
                 "only after the window below, rolling FIFO (C6 §4). "
                 "Off: legacy behaviour — each month is independent and "
                 "uncommitted tickets simply vanish.",
        )
        expiry_days = ec2.number_input(
            "Ticket expiry (days)", min_value=30, max_value=365,
            value=RAFFLE_TICKET_EXPIRY_DAYS, step=30,
            help="C6 §4: 60 days from issuance, rolling FIFO.",
            disabled=not model_expiry,
        )
        ec3.metric(
            "Pity threshold",
            f"{RAFFLE_PITY_THRESHOLD} losing draws",
            help="C6 §8, revised 3 → 2 in July 2026 so the guarantee lands "
                 "inside the 60-day ticket window. A lower threshold puts more "
                 "users in the pity state, raising Consolation-tier load.",
        )
        if model_expiry:
            _sp = max(1, int(round(expiry_days / RAFFLE_DRAW_PERIOD_DAYS)))
            st.caption(
                f"A ticket survives **{_sp} draw(s)** at a {RAFFLE_DRAW_PERIOD_DAYS}-day cadence. "
                f"Carry-over makes the committed pool T larger than one month's earnings, "
                f"which lowers each committer's win probability at an unchanged prize budget."
            )

        # ---- Per-month scenario grid (long format: 6 tier rows per month) ----
        st.markdown("**Per-month scenario grid — single source of truth.**")
        st.info(
            "This grid **overrides** the seed inputs above. Each month has **six tier rows** "
            "(Scout → Legend, per Component 2 §5.2). Edit them to model month-specific scenarios:\n\n"
            "- **N Users** — total active users that month (a per-month value; edit it on the "
            "first tier row of the month, it applies to all six).\n"
            "- **Profile (%)** — that month's share of users in each tier. The six values "
            "**should sum to 100%** per month (auto-normalised if not).\n"
            "- **Tickets** — monthly tickets earned by a user in that tier (engagement-driven; "
            "scanning earns none).\n"
            "- **Commit Rate (%)** — share of that tier's users who commit ≥1 ticket to the draw.\n\n"
            "Per month: `N_active = Σ tier_users × commit_rate`. The committed pool `T` is drawn "
            "from each tier's running ticket balance when expiry is modelled, or equals "
            "`Σ tier_committers × tier_tickets` when it is not.",
            icon="🧭",
        )

        # Build long-format seed: months × 4 tiers
        seed_rows = []
        for mth in range(1, int(n_months_raffle) + 1):
            for tier in RAFFLE_TIER_SEED:
                seed_rows.append({
                    "Month":           mth,
                    "N Users":         int(default_users),
                    "Tier":            tier["Tier"],
                    "Profile (%)":     tier["Profile (%)"],
                    "Tickets":         tier["Tickets"],
                    "Commit Rate (%)": tier["Commit Rate (%)"],
                })
        grid_seed = pd.DataFrame(seed_rows)

        grid_df = st.data_editor(
            grid_seed, hide_index=True, use_container_width=True, key="raffle_grid",
            height=min(560, 38 * len(grid_seed) + 40),
            column_config={
                "Month": st.column_config.NumberColumn(disabled=True),
                "Tier":  st.column_config.TextColumn(disabled=True),
                "N Users": st.column_config.NumberColumn(format="%d", min_value=0),
                "Profile (%)": st.column_config.NumberColumn(format="%.1f %%", min_value=0.0, max_value=100.0),
                "Tickets": st.column_config.NumberColumn(format="%d", min_value=0),
                "Commit Rate (%)": st.column_config.NumberColumn(format="%.1f %%", min_value=0.0, max_value=100.0),
            },
        )

        # Per-month Profile% sum sanity check
        bad_months = []
        for mth, g in grid_df.groupby("Month"):
            if abs(g["Profile (%)"].sum() - 100.0) > 0.1:
                bad_months.append(int(mth))
        if bad_months:
            st.caption(f"ℹ️ Profile (%) doesn't sum to 100% in month(s) "
                       f"{', '.join(map(str, bad_months[:8]))}{'…' if len(bad_months) > 8 else ''} "
                       f"— shares are auto-normalised for those months.")

        raffle_df = run_raffle_simulation(
            grid_df, raffle_pool_tokens,
            mid_floor=int(mid_floor), mid_ceil=int(mid_ceil),
            cons_floor=int(cons_floor), cons_ceil=int(cons_ceil),
            model_expiry=bool(model_expiry),
            expiry_days=int(expiry_days),
        )

        # ---- Ticket lifecycle summary ----
        if model_expiry and "Tickets Expired" in raffle_df.columns:
            lc1, lc2, lc3 = st.columns(3)
            _earned = raffle_df["Tickets Earned"].sum()
            _expired = raffle_df["Tickets Expired"].sum()
            lc1.metric("Tickets earned (total)", f"{_earned:,.0f}")
            lc2.metric(
                "Tickets expired unused", f"{_expired:,.0f}",
                delta=f"{(100 * _expired / _earned if _earned else 0):.1f}% of earned",
                delta_color="inverse",
                help="Tickets burned before their holder committed them. A high "
                     "share suggests the expiry window is too tight, or commit "
                     "rates are too low for the tickets being issued.",
            )
            lc3.metric(
                "Balance carried (final month)",
                f"{raffle_df['Balance Carried'].iloc[-1]:,.0f}",
                help="Unexpired, uncommitted tickets still in user balances at "
                     "the end of the simulation window.",
            )

        st.markdown("---")

        # ---- Charts ----
        st.plotly_chart(fig_raffle_tokens(raffle_df, params.raffle_pct), use_container_width=True)
        st.plotly_chart(fig_prize_tiers(raffle_df), use_container_width=True)

        st.plotly_chart(fig_tickets_farmed_committed(raffle_df), use_container_width=True)

        # Reference month drives the probability & expected-winnings views
        ref_month = st.select_slider(
            "Reference month for probability & expected-winnings charts",
            options=raffle_df["Month"].tolist(),
            value=int(raffle_df["Month"].iloc[-1]),
            help="These two charts are snapshots of a single draw. Pick which month's "
                 "pool (N_active, T, winner slots) to evaluate.",
        )
        # Per-profile tickets for the reference month (from that month's tier rows)
        ref_rows = grid_df[grid_df["Month"] == ref_month]
        ref_profiles = pd.DataFrame({
            "Profile":    ref_rows["Tier"].tolist(),
            "Tickets/mo": ref_rows["Tickets"].tolist(),
        })

        col_a, col_b = st.columns(2)
        with col_a:
            st.plotly_chart(fig_win_prob_curve(raffle_df, ref_month), use_container_width=True)
        with col_b:
            st.plotly_chart(fig_expected_winnings(raffle_df, ref_profiles, ref_month), use_container_width=True)

        st.plotly_chart(fig_pity_population(raffle_df), use_container_width=True)

        with st.expander("📋 Full raffle simulation data table"):
            st.dataframe(raffle_df, hide_index=True, use_container_width=True)

    # ================================================================
    # TAB 4 — TESTNET
    # ================================================================
    with tab_testnet:

        st.markdown(
            f"Testnet rates = mainnet base rates × **α = {params.testnet_alpha}**. "
            f"Pool: **{params.testnet_pool_m:.0f}M \\$AVA** — "
            f"**{metrics.tn_tge_m:.0f}M ({params.testnet_tge_pct:.0f}%) unlocked at TGE**, "
            f"remaining {params.testnet_pool_m - metrics.tn_tge_m:.0f}M vesting "
            f"**{metrics.tn_vest_monthly_m:.1f}M / month** over "
            f"{params.testnet_months} months "
            f"(average {metrics.tn_monthly_budget_m:.1f}M / month)."
        )

        # ---- Testnet key metrics ----
        t1, t2, t3, t4 = st.columns(4)
        t1.metric(
            "Testnet new scan reward",
            f"{metrics.tn_new_scan_tokens:,.0f} $AVA",
            f"€{metrics.tn_new_scan_tokens * params.token_price_eur:.2f}",
            help=(
                f"Mainnet base rate × α ({params.testnet_alpha}×). "
                "Higher rate incentivises early testers to scan more broadly and stress-test "
                "the scan pipeline before mainnet launch."
            ),
        )
        t2.metric(
            "Testnet duplicate reward",
            f"{metrics.tn_dup_tokens:,.1f} $AVA",
            f"€{metrics.tn_dup_tokens * params.token_price_eur:.3f}",
            help=(
                f"Mainnet duplicate rate × α ({params.testnet_alpha}×). "
                "Duplicate scans on testnet still validate the data pipeline and "
                "peer-verification flow, so they are rewarded at the same multiplier."
            ),
        )
        t3.metric(
            "TGE unlock + monthly vesting",
            f"{metrics.tn_tge_m:.0f}M + {metrics.tn_vest_monthly_m:.1f}M/mo",
            help=(
                f"{params.testnet_tge_pct:.0f}% of the testnet pool "
                f"({metrics.tn_tge_m:.0f}M $AVA) unlocks at TGE; the remaining "
                f"{params.testnet_pool_m - metrics.tn_tge_m:.0f}M vests linearly over "
                f"{params.testnet_months} months. Front-loaded availability per the "
                "allocation sheet — not a flat pool ÷ months average."
            ),
        )
        max_new_scans_per_month = int(
            metrics.tn_monthly_budget_m * 1e6 / max(metrics.tn_new_scan_tokens, 1)
        )
        t4.metric(
            "Max new scans before pool exhausts",
            f"{max_new_scans_per_month:,} / month",
            help=(
                "Upper bound assuming all testnet spend goes to new scans only. "
                "Real capacity is lower because duplicate scans also draw from the pool. "
                "Use this as a rough ceiling when deciding how many testers to onboard."
            ),
        )

        st.markdown("---")

        # Pool depletion chart by tester count
        st.plotly_chart(fig_testnet_burn(params, metrics), use_container_width=True)

        # ---- Tester sensitivity table ----
        st.markdown("**Testnet pool sensitivity by active tester count**")
        st.caption(
            "Assumes each active tester: 5 new scans/month + 20 duplicate scans/month."
        )

        tester_volumes = [100, 500, 1_000, 2_000, 5_000, 10_000, 20_000]
        tbl_rows = []
        for v in tester_volumes:
            monthly_m = (
                v * 5  * metrics.tn_new_scan_tokens
                + v * 20 * metrics.tn_dup_tokens
            ) / 1e6
            # Vesting-aware runway: TGE unlock up front, monthly tranches during
            # the window. Runway = first month available capital is exhausted.
            bal = metrics.tn_tge_m
            runway = None
            for m in range(1, params.testnet_months * 4 + 1):
                inflow = metrics.tn_vest_monthly_m if m <= params.testnet_months else 0.0
                bal = bal + inflow - monthly_m
                if bal < 0:
                    runway = m
                    break
            survives = runway is None or runway > params.testnet_months
            tbl_rows.append({
                "Active testers":              f"{v:,}",
                "Monthly spend (M $AVA)":      f"{monthly_m:.3f}",
                "Pool runway (months)":
                    f"> {params.testnet_months * 4}" if runway is None else f"{runway}",
                "Survives testnet window?":
                    "✅" if survives else "⚠️ Depletes early",
            })
        st.dataframe(pd.DataFrame(tbl_rows), hide_index=True, use_container_width=True)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    main()
