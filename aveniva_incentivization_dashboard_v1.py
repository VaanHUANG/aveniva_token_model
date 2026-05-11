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

    # ---- Hard cap ---------------------------------------------------------
    hard_cap_new_scan: int      # Maximum $AVA per new scan; excess scan budget flows to RC

    # ---- Simulation volume curve ------------------------------------------
    spike_multiplier: float     # Volume multiplier during the launch spike window
    spike_months: int           # How many months the full spike lasts
    decay_months: int           # Months to linearly decay from spike back to 1×

    # ---- RC combined timeline chart ---------------------------------------
    testnet_chart_users: int    # Active testnet users assumed for the RC timeline chart


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

    # Testnet rates
    tn_new_scan_tokens: float
    tn_dup_tokens: float
    tn_monthly_budget_m: float      # Testnet pool / testnet months


# ==============================================================================
# 3. CALCULATION ENGINE
# ==============================================================================

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

    # --- Testnet rates ---
    tn_new = base_new * p.testnet_alpha
    tn_dup = base_dup * p.testnet_alpha
    tn_monthly_budget_m = p.testnet_pool_m / max(p.testnet_months, 1)

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
        tn_new_scan_tokens        = tn_new,
        tn_dup_tokens             = tn_dup,
        tn_monthly_budget_m       = tn_monthly_budget_m,
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

    # Hard-capped scan rates (applied BEFORE the soft cap and BEFORE floor)
    hc_new_rate = min(d.base_new_scan_tokens, float(p.hard_cap_new_scan))
    hc_dup_rate = hc_new_rate / max(p.dup_divisor, 1.0)

    for month in range(1, n_months + 1):

        # ---- 1. Volume multiplier for this month ----
        if month <= p.spike_months:
            vol_mult = p.spike_multiplier
        elif month <= p.spike_months + p.decay_months:
            progress = (month - p.spike_months) / max(p.decay_months, 1)
            vol_mult = p.spike_multiplier + progress * (1.0 - p.spike_multiplier)
        else:
            vol_mult = 1.0

        new_scans = p.new_scans_monthly * vol_mult
        dup_scans = p.dup_scans_monthly * vol_mult

        # ---- 2. Hard cap savings (tokens not paid out → flow to RC) ----
        # This is the difference between what the pool rate would pay and what the hard cap allows.
        uncapped_new = d.base_new_scan_tokens
        uncapped_dup = d.base_dup_tokens
        hc_savings = max(0.0, (
            (uncapped_new - hc_new_rate) * new_scans
            + (uncapped_dup - hc_dup_rate) * dup_scans
        ))   # in raw tokens

        # ---- 3. Scan accruals at hard-capped rates ----
        raw_scan_accruals = (
            new_scans * hc_new_rate
            + dup_scans * hc_dup_rate
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
        eff_new_scan = max(hc_new_rate * scaling, float(p.floor_new_scan))
        eff_dup      = max(hc_dup_rate * scaling, p.floor_new_scan / max(p.dup_divisor, 1.0))
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
      at testnet rates (α-scaled).  Pool starts at p.testnet_pool_m.

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

    tn_balances = [p.testnet_pool_m]          # T M0 = full pool
    bal = p.testnet_pool_m
    for _ in range(p.testnet_months):
        bal = max(0.0, bal - monthly_tn_spend)
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

        balance = p.testnet_pool_m
        balances = []
        for _ in range(p.testnet_months + 3):   # a few months beyond window
            balance = max(0, balance - monthly_spend)
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

    # ---- Price & Floor ------------------------------------------
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
        token_price_eur     = token_price_eur,
        floor_new_scan      = floor_new_scan,
        hard_cap_new_scan   = hard_cap_new_scan,
        testnet_alpha       = testnet_alpha,
        testnet_pool_m      = testnet_pool_m,
        testnet_months      = testnet_months,
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
    tab_overview, tab_sim, tab_testnet = st.tabs([
        "📊 Overview & Rates",
        "📉 Simulation",
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

            # Scan budget utilisation health check
            new_cost_m  = params.new_scans_monthly * metrics.base_new_scan_tokens / 1e6
            dup_cost_m  = params.dup_scans_monthly  * metrics.base_dup_tokens      / 1e6
            total_scan_m = new_cost_m + dup_cost_m
            utilisation  = total_scan_m / max(metrics.scan_budget_m, 0.001) * 100

            st.markdown("**Scan bucket utilisation at base volume**")
            u1, u2, u3 = st.columns(3)
            u1.metric(
                "New scan cost",
                f"{new_cost_m:.2f}M $AVA",
                help="Monthly $AVA spent on new-scan rewards at steady-state volume.",
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
    # TAB 3 — TESTNET
    # ================================================================
    with tab_testnet:

        st.markdown(
            f"Testnet rates = mainnet base rates × **α = {params.testnet_alpha}**. "
            f"Budget: **{params.testnet_pool_m:.0f}M \\$AVA** over "
            f"**{params.testnet_months} months** "
            f"= **{metrics.tn_monthly_budget_m:.1f}M \\$AVA / month**."
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
            "Testnet monthly budget",
            f"{metrics.tn_monthly_budget_m:.1f}M $AVA",
            help=(
                f"Testnet pool ({params.testnet_pool_m:.0f}M $AVA) ÷ "
                f"testnet months ({params.testnet_months}). "
                "This is the average monthly budget available; in practice spend depends "
                "on how many testers are active and how many scans they submit."
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
            runway = params.testnet_pool_m / max(monthly_m, 0.001)
            tbl_rows.append({
                "Active testers":              f"{v:,}",
                "Monthly spend (M $AVA)":      f"{monthly_m:.3f}",
                "Pool runway (months)":        f"{runway:.1f}",
                "Survives testnet window?":
                    "✅" if runway >= params.testnet_months else "⚠️ Depletes early",
            })
        st.dataframe(pd.DataFrame(tbl_rows), hide_index=True, use_container_width=True)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    main()
