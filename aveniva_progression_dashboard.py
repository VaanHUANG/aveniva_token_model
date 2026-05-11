"""
aveniva_progression_dashboard.py
==================================
Aveniva — Component 2 Progression System Visualizations

Four tabs:
  1. Track Level Curves  — per-track XP thresholds (L1–L5), all 10 tracks
  2. Overall Level Curve — the 30-level total-XP curve with tier shading
  3. Multiplier Curves   — scan reward and raffle ticket multipliers by level
  4. Level Calculator    — enter XP per track to see your current level and benefits

Run:
    pip install streamlit plotly pandas
    streamlit run aveniva_progression_dashboard.py

Share with investors:
    Push to a public GitHub repo → https://share.streamlit.io → Deploy
"""

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

# ==============================================================================
# 1. PAGE CONFIG
# ==============================================================================

st.set_page_config(
    page_title="Aveniva — Progression System",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ==============================================================================
# 2. COLOUR PALETTE
# ==============================================================================

# One colour per tier (6 tiers)
TIER_COLORS = {
    "Scout":     "#6C9BD2",
    "Taster":    "#5BAD8B",
    "Collector": "#E8C840",
    "Analyst":   "#E8834A",
    "Expert":    "#C1558B",
    "Legend":    "#9B59B6",
}

# One colour per track (10 tracks)
TRACK_COLORS = [
    "#E74C3C",  # Junk Food Master
    "#3498DB",  # Beverage Baron
    "#2ECC71",  # Health Guru
    "#F39C12",  # Global Foodie
    "#E67E22",  # Streak King
    "#1ABC9C",  # Volume Ace
    "#9B59B6",  # Label Detective
    "#27AE60",  # Verification Sage
    "#E91E63",  # Network Builder
    "#00BCD4",  # Territory Pioneer
]

# ==============================================================================
# 3. STATIC DATA  (mirrors the Component 2 specification exactly)
# ==============================================================================

# ---- 10 tracks ----
TRACKS = [
    {"name": "🍟 Junk Food Master",  "category": "Product Domain",       "type": "Standard"},
    {"name": "🥤 Beverage Baron",     "category": "Product Domain",       "type": "Standard"},
    {"name": "🥗 Health Guru",        "category": "Product Domain",       "type": "Standard"},
    {"name": "🌍 Global Foodie",      "category": "Product Domain",       "type": "Elite"},
    {"name": "🔥 Streak King",        "category": "Scanning Consistency", "type": "Standard"},
    {"name": "⚡ Volume Ace",         "category": "Scanning Consistency", "type": "Elite"},
    {"name": "🏷 Label Detective",    "category": "Data Quality",         "type": "Standard"},
    {"name": "✅ Verification Sage",  "category": "Data Quality",         "type": "Standard"},
    {"name": "🤝 Network Builder",    "category": "Network & Discovery",  "type": "Elite"},
    {"name": "🗺 Territory Pioneer",  "category": "Network & Discovery",  "type": "Elite"},
]

# ---- Per-track level thresholds (cumulative XP within that track) ----
# Standard = 6 tracks; Elite = 4 tracks
STANDARD_THRESH = [0, 500,   2_000,  6_000,  15_000]   # L1 – L5
ELITE_THRESH    = [0, 800,   3_500,  10_000, 25_000]    # L1 – L5
LEVEL_LABELS    = ["L1", "L2", "L3", "L4", "L5"]

def track_thresh(track: dict) -> list:
    return STANDARD_THRESH if track["type"] == "Standard" else ELITE_THRESH

# ---- Overall level thresholds (total XP across all tracks) ----
# 30 values; index i → Level (i+1)
OVERALL_XP = [
          0,   # L1  ── Scout ──
        250,   # L2
        600,   # L3
      1_200,   # L4
      2_200,   # L5
      3_700,   # L6  ── Taster ──
      5_700,   # L7
      8_200,   # L8
     11_500,   # L9
     15_500,   # L10
     21_000,   # L11 ── Collector ──
     28_000,   # L12
     37_000,   # L13
     48_500,   # L14
     63_000,   # L15
     81_000,   # L16 ── Analyst ──
    103_000,   # L17
    130_000,   # L18
    162_000,   # L19
    200_000,   # L20
    245_000,   # L21 ── Expert ──
    298_000,   # L22
    360_000,   # L23
    432_000,   # L24
    515_000,   # L25
    610_000,   # L26 ── Legend ──
    720_000,   # L27
    847_000,   # L28
    993_000,   # L29
  1_160_000,   # L30
]

# ---- 6 named tiers ----
TIERS = [
    {"name": "Scout",     "levels": (1,  5),  "scan_mult": 1.00, "raffle_mult": 1.0},
    {"name": "Taster",    "levels": (6,  10), "scan_mult": 1.15, "raffle_mult": 1.2},
    {"name": "Collector", "levels": (11, 15), "scan_mult": 1.30, "raffle_mult": 1.5},
    {"name": "Analyst",   "levels": (16, 20), "scan_mult": 1.50, "raffle_mult": 2.0},
    {"name": "Expert",    "levels": (21, 25), "scan_mult": 1.75, "raffle_mult": 2.5},
    {"name": "Legend",    "levels": (26, 30), "scan_mult": 2.00, "raffle_mult": 3.0},
]

# ---- Level-up $AVA milestone payouts (at tier boundaries) ----
LEVELUP_PAYOUTS = {5: 500, 10: 1_500, 15: 4_000, 20: 10_000, 25: 25_000, 30: 60_000}

# ---- User archetypes for time-to-level chart ----
ARCHETYPES = [
    {"name": "Casual  (~700 XP/mo)",    "xpm": 700,    "color": TIER_COLORS["Taster"],    "dash": "dot"},
    {"name": "Active  (~4,500 XP/mo)",  "xpm": 4_500,  "color": TIER_COLORS["Analyst"],   "dash": "solid"},
    {"name": "Power   (~12,000 XP/mo)", "xpm": 12_000, "color": TIER_COLORS["Legend"],    "dash": "dash"},
]

# ==============================================================================
# 4. HELPER FUNCTIONS
# ==============================================================================

def get_tier(level: int) -> dict:
    """Return the tier dict for a given overall level."""
    for t in TIERS:
        if t["levels"][0] <= level <= t["levels"][1]:
            return t
    return TIERS[-1]


def get_overall_level(total_xp: int) -> int:
    """Return overall level (1-30) for a given total XP."""
    level = 1
    for i, threshold in enumerate(OVERALL_XP):
        if total_xp >= threshold:
            level = i + 1
        else:
            break
    return min(level, 30)


def level_progress(total_xp: int):
    """Return (level, xp_within_level, xp_to_next_level)."""
    lvl = get_overall_level(total_xp)
    if lvl >= 30:
        return 30, total_xp - OVERALL_XP[29], 0
    floor_curr = OVERALL_XP[lvl - 1]
    floor_next = OVERALL_XP[lvl]
    return lvl, total_xp - floor_curr, floor_next - floor_curr


def track_level(xp_in_track: int, track: dict) -> int:
    """Return track level (1-5) for given XP earned within that track."""
    thresh = track_thresh(track)
    lvl = 1
    for i, t in enumerate(thresh):
        if xp_in_track >= t:
            lvl = i + 1
    return min(lvl, 5)


# ==============================================================================
# 5. CHART FUNCTIONS
# ==============================================================================

# ---- TAB 1: Track Level Curves -----------------------------------------------

def fig_track_cumulative() -> go.Figure:
    """
    Line chart: cumulative XP needed to reach each track level (L1–L5).
    All 10 tracks on one chart — Standard tracks solid, Elite tracks dashed.
    """
    fig = go.Figure()
    x_vals = [1, 2, 3, 4, 5]

    for i, track in enumerate(TRACKS):
        thresh = track_thresh(track)
        dash   = "solid" if track["type"] == "Standard" else "dash"
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=thresh,
            mode="lines+markers",
            name=track["name"],
            line=dict(color=TRACK_COLORS[i], width=2.2, dash=dash),
            marker=dict(size=8, color=TRACK_COLORS[i]),
            hovertemplate=(
                f"<b>{track['name']}</b><br>"
                "Level %{x}<br>"
                "Cumulative XP: %{y:,}<extra></extra>"
            ),
        ))

    fig.update_layout(
        title="Per-Track Cumulative XP Thresholds (L1 → L5)",
        xaxis=dict(
            title="Track level",
            tickmode="array", tickvals=x_vals, ticktext=LEVEL_LABELS,
        ),
        yaxis_title="Cumulative XP within track",
        legend=dict(
            orientation="v", x=1.02, y=1, xanchor="left",
            font=dict(size=11),
        ),
        margin=dict(t=60, b=60, l=75, r=220),
        height=470,
        annotations=[dict(
            x=0.0, y=-0.12, xref="paper", yref="paper",
            text="Solid lines = Standard tracks (6)     ·     Dashed lines = Elite tracks (4)",
            showarrow=False, font=dict(size=11, color="#aaaaaa"), align="left",
        )],
    )
    return fig


def fig_track_increments() -> go.Figure:
    """
    Grouped bar chart: XP increment per level step (Standard vs Elite).
    Makes the 'cost to level up' easy to compare between track types.
    """
    steps   = ["L1 → L2", "L2 → L3", "L3 → L4", "L4 → L5"]
    std_inc  = [STANDARD_THRESH[i+1] - STANDARD_THRESH[i] for i in range(4)]
    eli_inc  = [ELITE_THRESH[i+1]    - ELITE_THRESH[i]    for i in range(4)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=steps, y=std_inc, name="Standard (6 tracks)",
        marker_color=TIER_COLORS["Scout"],
        text=[f"{v:,}" for v in std_inc], textposition="outside",
        hovertemplate="Standard · %{x}: <b>%{y:,} XP</b><extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=steps, y=eli_inc, name="Elite (4 tracks)",
        marker_color=TIER_COLORS["Expert"],
        text=[f"{v:,}" for v in eli_inc], textposition="outside",
        hovertemplate="Elite · %{x}: <b>%{y:,} XP</b><extra></extra>",
    ))
    fig.update_layout(
        title="XP Increment Per Level Step: Standard vs Elite Tracks",
        barmode="group",
        xaxis_title="Level transition",
        yaxis_title="XP increment",
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=60, b=80, l=75, r=20),
        height=360,
    )
    return fig


# ---- TAB 2: Overall Level Curve ----------------------------------------------

def fig_overall_curve() -> go.Figure:
    """
    Line chart: total XP needed for each of the 30 overall levels.
    Tier regions shaded. Gold stars = milestone payout levels.
    """
    levels  = list(range(1, 31))
    xp_vals = OVERALL_XP

    fig = go.Figure()

    # Tier background shading + tier name annotations
    for tier in TIERS:
        ls, le = tier["levels"]
        color  = TIER_COLORS[tier["name"]]
        fig.add_vrect(
            x0=ls - 0.5, x1=le + 0.5,
            fillcolor=color, opacity=0.09, line_width=0,
        )
        fig.add_annotation(
            x=(ls + le) / 2, y=1.06,
            xref="x", yref="paper",
            text=f"<b>{tier['name']}</b>",
            showarrow=False,
            font=dict(size=11, color=color),
        )

    # Milestone star markers
    for lvl, payout in LEVELUP_PAYOUTS.items():
        fig.add_trace(go.Scatter(
            x=[lvl], y=[OVERALL_XP[lvl - 1]],
            mode="markers",
            name=f"L{lvl} → {payout:,} $AVA payout",
            marker=dict(size=16, color="gold", symbol="star",
                        line=dict(width=1.5, color="white")),
            showlegend=True,
            hovertemplate=(
                f"<b>Level {lvl} milestone</b><br>"
                f"XP: {OVERALL_XP[lvl-1]:,}<br>"
                f"Payout: {payout:,} $AVA<extra></extra>"
            ),
        ))

    # Main curve
    fig.add_trace(go.Scatter(
        x=levels, y=xp_vals,
        mode="lines+markers",
        name="Total XP needed",
        line=dict(color="white", width=2.5),
        marker=dict(size=5),
        hovertemplate="<b>Level %{x}</b><br>Total XP: %{y:,}<extra></extra>",
        showlegend=False,
    ))

    fig.update_layout(
        title="Overall Contributor Level Curve (Levels 1–30)",
        xaxis=dict(
            title="Overall level",
            tickmode="array",
            tickvals=list(range(1, 31, 2)),
            ticktext=[str(i) for i in range(1, 31, 2)],
        ),
        yaxis_title="Total XP (all tracks combined)",
        legend=dict(
            orientation="v", x=1.02, y=1, xanchor="left", font=dict(size=10),
        ),
        margin=dict(t=70, b=60, l=85, r=210),
        height=490,
    )
    return fig


def fig_xp_increment_per_level() -> go.Figure:
    """
    Bar chart: XP needed to go from level N to level N+1.
    Bars coloured by tier — shows how the 'cost of levelling up' grows.
    """
    levels     = list(range(1, 30))
    increments = [OVERALL_XP[i] - OVERALL_XP[i - 1] for i in range(1, 30)]
    colors     = [TIER_COLORS[get_tier(l)["name"]] for l in levels]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=levels, y=increments,
        marker_color=colors,
        hovertemplate=(
            "L%{x} → L%{customdata}<br>"
            "XP needed: <b>%{y:,}</b><extra></extra>"
        ),
        customdata=[l + 1 for l in levels],
        showlegend=False,
    ))

    # Tier boundary vertical dashed lines
    for tier in TIERS[1:]:
        ls = tier["levels"][0]
        fig.add_vline(
            x=ls - 0.5, line_dash="dot",
            line_color=TIER_COLORS[tier["name"]], opacity=0.75,
            annotation_text=tier["name"],
            annotation_position="top right",
            annotation_font_color=TIER_COLORS[tier["name"]],
            annotation_font_size=10,
        )

    fig.update_layout(
        title="XP Cost of Each Level-Up (L1→L2 through L29→L30)",
        xaxis=dict(
            title="From level",
            tickmode="array",
            tickvals=list(range(1, 30, 2)),
        ),
        yaxis_title="XP increment",
        margin=dict(t=60, b=60, l=80, r=20),
        height=360,
    )
    return fig


def fig_time_to_level() -> go.Figure:
    """
    Line chart: expected months to reach each level for three user archetypes.
    Horizontal reference lines at 12, 24, 36 months.
    """
    levels = list(range(1, 31))
    fig    = go.Figure()

    for archetype in ARCHETYPES:
        months = [OVERALL_XP[l - 1] / archetype["xpm"] for l in levels]
        fig.add_trace(go.Scatter(
            x=levels, y=months,
            mode="lines",
            name=archetype["name"],
            line=dict(color=archetype["color"], width=2.5, dash=archetype["dash"]),
            hovertemplate=(
                f"<b>{archetype['name']}</b><br>"
                "Level %{x}<br>~%{y:.1f} months<extra></extra>"
            ),
        ))

    for tier in TIERS[1:]:
        fig.add_vline(
            x=tier["levels"][0] - 0.5, line_dash="dot",
            line_color="grey", opacity=0.35,
        )

    for ref_months, label in [(12, "1 year"), (24, "2 years"), (36, "3 years"), (60, "5 years")]:
        fig.add_hline(
            y=ref_months, line_dash="dot", line_color="grey", opacity=0.45,
            annotation_text=label, annotation_position="right",
            annotation_font_size=10,
        )

    fig.update_layout(
        title="Expected Months to Reach Each Level by User Archetype",
        xaxis=dict(
            title="Overall level",
            tickmode="array", tickvals=list(range(1, 31, 2)),
        ),
        yaxis_title="Months from start",
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=60, b=80, l=75, r=80),
        height=420,
    )
    return fig


# ---- TAB 3: Multiplier Curves ------------------------------------------------

def fig_multiplier_combined(base_hard_cap: int = 250) -> go.Figure:
    """
    Dual-axis step chart:
      Left  — scan reward multiplier (1.00× → 2.00×) and effective $AVA cap
      Right — raffle ticket multiplier (1.0× → 3.0×)
    Tier regions shaded. Step shape makes tier boundaries explicit.
    """
    levels       = list(range(1, 31))
    scan_mults   = [get_tier(l)["scan_mult"]          for l in levels]
    raffle_mults = [get_tier(l)["raffle_mult"]         for l in levels]
    scan_caps    = [m * base_hard_cap                  for m in scan_mults]
    tier_colors  = [TIER_COLORS[get_tier(l)["name"]]  for l in levels]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Tier shading + labels
    for tier in TIERS:
        ls, le = tier["levels"]
        color  = TIER_COLORS[tier["name"]]
        fig.add_vrect(
            x0=ls - 0.5, x1=le + 0.5,
            fillcolor=color, opacity=0.08, line_width=0,
        )
        fig.add_annotation(
            x=(ls + le) / 2, y=1.07,
            xref="x", yref="paper",
            text=f"<b>{tier['name']}</b>",
            showarrow=False,
            font=dict(size=11, color=color),
        )

    # Scan multiplier — left axis
    fig.add_trace(go.Scatter(
        x=levels, y=scan_mults,
        mode="lines+markers",
        name="Scan reward multiplier",
        line=dict(color="#5BAD8B", width=3, shape="hv"),
        marker=dict(size=7, color=tier_colors),
        hovertemplate=(
            "Level %{x}<br>"
            "Scan mult: <b>%{y:.2f}×</b><br>"
            "Effective cap: <b>%{customdata:,} $AVA</b><extra></extra>"
        ),
        customdata=scan_caps,
    ), secondary_y=False)

    # Effective scan cap (dotted, hidden by default — toggle in legend)
    fig.add_trace(go.Scatter(
        x=levels, y=scan_caps,
        mode="lines",
        name=f"Effective scan cap ($AVA, base={base_hard_cap})",
        line=dict(color="#5BAD8B", width=1.5, dash="dot", shape="hv"),
        hovertemplate="Level %{x}<br>Scan cap: <b>%{y:,} $AVA</b><extra></extra>",
        visible="legendonly",
    ), secondary_y=False)

    # Raffle multiplier — right axis
    fig.add_trace(go.Scatter(
        x=levels, y=raffle_mults,
        mode="lines+markers",
        name="Raffle ticket multiplier",
        line=dict(color=TIER_COLORS["Expert"], width=3, dash="dash", shape="hv"),
        marker=dict(size=7, color=tier_colors, symbol="diamond"),
        hovertemplate="Level %{x}<br>Raffle mult: <b>%{y:.1f}×</b><extra></extra>",
    ), secondary_y=True)

    fig.update_yaxes(title_text="Scan reward multiplier  (left)", secondary_y=False)
    fig.update_yaxes(title_text="Raffle ticket multiplier  (right)", secondary_y=True)
    fig.update_layout(
        title=f"Scan Multiplier & Raffle Ticket Multiplier by Level  (base cap = {base_hard_cap} $AVA)",
        xaxis=dict(
            title="Overall level",
            tickmode="array", tickvals=list(range(1, 31, 2)),
        ),
        legend=dict(orientation="h", y=-0.22),
        margin=dict(t=70, b=90, l=80, r=80),
        height=470,
    )
    return fig


def fig_scan_cap_bars(base_hard_cap: int = 250) -> go.Figure:
    """
    Bar chart: effective per-scan $AVA cap at each level.
    Bars coloured by tier. Tier cap value annotated once per tier block.
    """
    levels    = list(range(1, 31))
    caps      = [get_tier(l)["scan_mult"] * base_hard_cap for l in levels]
    colors    = [TIER_COLORS[get_tier(l)["name"]]         for l in levels]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=levels, y=caps,
        marker_color=colors,
        hovertemplate=(
            "Level %{x} (%{customdata})<br>"
            "Scan cap: <b>%{y:,.0f} $AVA</b><extra></extra>"
        ),
        customdata=[get_tier(l)["name"] for l in levels],
        showlegend=False,
    ))

    # Annotate one value per tier
    for tier in TIERS:
        ls, le  = tier["levels"]
        cap_val = tier["scan_mult"] * base_hard_cap
        color   = TIER_COLORS[tier["name"]]
        fig.add_annotation(
            x=(ls + le) / 2,
            y=cap_val + base_hard_cap * 0.05,
            text=f"<b>{cap_val:,.0f}</b>",
            showarrow=False,
            font=dict(size=11, color=color),
        )

    fig.update_layout(
        title=f"Effective Per-Scan $AVA Cap by Level  (base = {base_hard_cap} $AVA)",
        xaxis=dict(
            title="Overall level",
            tickmode="array", tickvals=list(range(1, 31, 2)),
        ),
        yaxis_title="Max $AVA earned per new scan",
        margin=dict(t=60, b=60, l=80, r=20),
        height=370,
    )
    return fig


def fig_raffle_bars() -> go.Figure:
    """
    Bar chart: raffle ticket multiplier at each level.
    Coloured by tier with multiplier values annotated per tier.
    """
    levels  = list(range(1, 31))
    mults   = [get_tier(l)["raffle_mult"] for l in levels]
    colors  = [TIER_COLORS[get_tier(l)["name"]] for l in levels]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=levels, y=mults,
        marker_color=colors,
        hovertemplate=(
            "Level %{x} (%{customdata})<br>"
            "Raffle mult: <b>%{y:.1f}×</b><extra></extra>"
        ),
        customdata=[get_tier(l)["name"] for l in levels],
        showlegend=False,
    ))

    for tier in TIERS:
        ls, le = tier["levels"]
        mult   = tier["raffle_mult"]
        fig.add_annotation(
            x=(ls + le) / 2,
            y=mult + 0.07,
            text=f"<b>{mult:.1f}×</b>",
            showarrow=False,
            font=dict(size=11, color=TIER_COLORS[tier["name"]]),
        )

    fig.update_layout(
        title="Raffle Ticket Multiplier by Level",
        xaxis=dict(
            title="Overall level",
            tickmode="array", tickvals=list(range(1, 31, 2)),
        ),
        yaxis_title="Raffle ticket multiplier",
        margin=dict(t=60, b=60, l=70, r=20),
        height=340,
    )
    return fig


# ==============================================================================
# 6. CALCULATOR TAB
# ==============================================================================

def render_calculator():
    """Interactive personal level calculator."""
    st.subheader("🧮 Where Am I? — Level Calculator")
    st.markdown(
        "Enter your XP earned in each track. The calculator sums all track XP "
        "to determine your overall level, progress bar, benefits, and upcoming milestones."
    )

    # XP inputs — 2-column grid
    track_xps: dict[str, int] = {}
    col_a, col_b = st.columns(2)
    for i, track in enumerate(TRACKS):
        col = col_a if i % 2 == 0 else col_b
        xp  = col.number_input(
            f"{track['name']}",
            min_value=0, max_value=1_000_000,
            value=0, step=100,
            key=f"xp_{i}",
            help=(
                f"XP earned within {track['name']}. "
                f"Track type: {track['type']} "
                f"(L5 threshold: {track_thresh(track)[4]:,} XP). "
                f"Category: {track['category']}."
            ),
        )
        track_xps[track["name"]] = xp

    total_xp             = sum(track_xps.values())
    overall_lvl, xp_in, xp_needed = level_progress(total_xp)
    tier                 = get_tier(overall_lvl)

    st.markdown("---")

    # ---- Summary metrics ----
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(
        "Total XP",
        f"{total_xp:,}",
        help="Arithmetic sum of all XP earned across all 10 tracks.",
    )
    m2.metric(
        "Overall Level",
        f"{overall_lvl}",
        f"Tier: {tier['name']}",
        help="Overall contributor level (1–30) derived from total XP.",
    )
    m3.metric(
        "Scan multiplier",
        f"{tier['scan_mult']:.2f}×",
        help="Multiplier applied to the base hard cap on per-scan $AVA rewards.",
    )
    m4.metric(
        "Raffle ticket multiplier",
        f"{tier['raffle_mult']:.1f}×",
        help="Raffle tickets earned per qualifying action are multiplied by this factor.",
    )
    m5.metric(
        "Effective scan cap",
        f"{int(tier['scan_mult'] * 250):,} $AVA",
        help="Effective max $AVA per new scan at current tier (assumes base cap = 250 $AVA).",
    )

    # ---- Progress bar to next overall level ----
    if overall_lvl < 30:
        next_lvl     = overall_lvl + 1
        next_tier    = get_tier(next_lvl)
        progress_pct = xp_in / xp_needed if xp_needed > 0 else 1.0
        tier_change  = f"  ·  Tier upgrades to **{next_tier['name']}**" if next_tier["name"] != tier["name"] else ""
        st.markdown(f"**Progress to Level {next_lvl}**{tier_change}")
        st.progress(min(progress_pct, 1.0))
        st.caption(
            f"{xp_in:,} / {xp_needed:,} XP within this level  "
            f"({xp_needed - xp_in:,} XP to go)"
        )
    else:
        st.success("🏆 Maximum level reached — you are a Legend!")

    # ---- Per-track level display ----
    st.markdown("---")
    st.markdown("**Per-Track Progress**")
    track_cols = st.columns(5)
    for i, track in enumerate(TRACKS):
        col         = track_cols[i % 5]
        xp          = track_xps[track["name"]]
        t_lvl       = track_level(xp, track)
        thresh      = track_thresh(track)
        # Progress within current track level
        if t_lvl < 5:
            t_curr  = thresh[t_lvl - 1]
            t_next  = thresh[t_lvl]
            prog    = (xp - t_curr) / (t_next - t_curr) if (t_next - t_curr) > 0 else 1.0
            sub_lbl = f"→ L{t_lvl + 1}: {t_next - xp:,} XP"
        else:
            prog    = 1.0
            sub_lbl = "✨ Mastered"
        col.metric(
            track["name"].split(" ", 1)[0],  # just the emoji
            f"L{t_lvl}",
            sub_lbl,
            help=f"{track['name']}: {xp:,} XP ({track['type']} track)",
        )
        col.progress(min(float(prog), 1.0))

    # ---- Milestone payouts earned / upcoming ----
    st.markdown("---")
    pc1, pc2 = st.columns(2)
    with pc1:
        st.markdown("**✅ Milestone $AVA Payouts Earned**")
        earned = {lvl: amt for lvl, amt in LEVELUP_PAYOUTS.items() if overall_lvl >= lvl}
        if earned:
            total_earned = sum(earned.values())
            for lvl, amt in earned.items():
                st.write(f"Level {lvl}: **{amt:,} $AVA**")
            st.caption(f"Total earned from milestones: **{total_earned:,} $AVA**")
        else:
            st.caption("None yet — first milestone at Level 5.")

    with pc2:
        st.markdown("**🎯 Upcoming Milestone Payouts**")
        upcoming = {lvl: amt for lvl, amt in LEVELUP_PAYOUTS.items() if overall_lvl < lvl}
        if upcoming:
            for lvl, amt in list(upcoming.items())[:4]:
                xp_gap = OVERALL_XP[lvl - 1] - total_xp
                st.write(f"Level {lvl}: **{amt:,} $AVA** — {xp_gap:,} XP away")
        else:
            st.success("All milestone payouts reached!")


# ==============================================================================
# 7. MAIN APP
# ==============================================================================

def main():
    st.title("🌿 Aveniva — Component 2: Progression System")
    st.markdown(
        "Visual reference for the XP and levelling system. "
        "All numbers match the Component 2 specification exactly. "
        "**Track Curves** — per-track thresholds (L1–L5)  |  "
        "**Overall Level** — the 30-level total-XP curve  |  "
        "**Multipliers** — scan cap and raffle ticket multipliers  |  "
        "**Calculator** — enter your XP to see your level."
    )
    st.markdown("---")

    tab_tracks, tab_overall, tab_mult, tab_calc = st.tabs([
        "🏅 Track Level Curves",
        "📈 Overall Level Curve",
        "✖️ Multiplier Curves",
        "🧮 Level Calculator",
    ])

    # ================================================================
    # TAB 1 — TRACK LEVEL CURVES
    # ================================================================
    with tab_tracks:
        st.markdown(
            "Each of the 10 tracks has 5 internal levels (L1–L5). XP is earned "
            "within the track only through that track's specific actions. "
            "**Standard tracks** (6) have lower thresholds; **Elite tracks** (4) "
            "require rarer actions and higher cumulative XP."
        )
        st.plotly_chart(fig_track_cumulative(), use_container_width=True)
        st.plotly_chart(fig_track_increments(), use_container_width=True)

        st.markdown("---")
        st.markdown("**Full Track Reference Table**")
        ref_rows = []
        for track in TRACKS:
            t = track_thresh(track)
            ref_rows.append({
                "Track":        track["name"],
                "Category":     track["category"],
                "Type":         track["type"],
                "L2 (XP)":  f"{t[1]:,}",
                "L3 (XP)":  f"{t[2]:,}",
                "L4 (XP)":  f"{t[3]:,}",
                "L5 (XP)":  f"{t[4]:,}",
                "L5 bonus":     "+30% XP + NFT badge",
            })
        st.dataframe(pd.DataFrame(ref_rows), hide_index=True, use_container_width=True)

    # ================================================================
    # TAB 2 — OVERALL LEVEL CURVE
    # ================================================================
    with tab_overall:
        st.markdown(
            "A user's **overall level (1–30)** is determined by their **total XP across all 10 tracks**. "
            "The curve is deliberately exponential: early levels are fast and satisfying; "
            "late levels require sustained, long-term commitment. "
            "Gold ★ stars mark the 6 tier-boundary levels where a $AVA milestone payout is distributed."
        )
        st.plotly_chart(fig_overall_curve(), use_container_width=True)
        st.plotly_chart(fig_xp_increment_per_level(), use_container_width=True)
        st.plotly_chart(fig_time_to_level(), use_container_width=True)

        st.markdown("---")
        st.markdown("**Full Level Reference Table**")
        level_rows = []
        for i, xp_thresh in enumerate(OVERALL_XP):
            lvl  = i + 1
            tier = get_tier(lvl)
            incr = OVERALL_XP[i] - OVERALL_XP[i - 1] if i > 0 else 0
            level_rows.append({
                "Level":            lvl,
                "Tier":             tier["name"],
                "XP to reach":      f"{xp_thresh:,}",
                "XP increment":     f"{incr:,}" if i > 0 else "—",
                "Scan mult":        f"{tier['scan_mult']:.2f}×",
                "Raffle mult":      f"{tier['raffle_mult']:.1f}×",
                "$AVA milestone":   f"{LEVELUP_PAYOUTS[lvl]:,}" if lvl in LEVELUP_PAYOUTS else "—",
            })
        st.dataframe(
            pd.DataFrame(level_rows),
            hide_index=True,
            use_container_width=True,
            height=520,
        )

    # ================================================================
    # TAB 3 — MULTIPLIER CURVES
    # ================================================================
    with tab_mult:
        st.markdown(
            "Both multipliers are **tier-based step functions** — they jump at levels 6, 11, 16, 21, and 26. "
            "The **scan multiplier** scales the hard cap on per-scan $AVA (set in Component 1). "
            "The **raffle multiplier** increases the tickets a contributor earns per qualifying action."
        )

        base_cap = st.number_input(
            "Base hard cap ($AVA per new scan, from Component 1 dashboard)",
            min_value=50, max_value=10_000, value=250, step=10,
            help=(
                "Adjust to see how all effective scan caps change proportionally across levels. "
                "Default 250 $AVA matches the Component 1 dashboard default."
            ),
        )

        st.plotly_chart(fig_multiplier_combined(base_cap), use_container_width=True)

        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(fig_scan_cap_bars(base_cap), use_container_width=True)
        with c2:
            st.plotly_chart(fig_raffle_bars(), use_container_width=True)

        st.markdown("---")
        st.markdown("**Tier Benefits Summary**")
        tier_rows = []
        for tier in TIERS:
            ls, le  = tier["levels"]
            xp_lo   = OVERALL_XP[ls - 1]
            xp_hi   = OVERALL_XP[le - 1]
            eff_cap = tier["scan_mult"] * base_cap
            tier_rows.append({
                "Tier":               tier["name"],
                "Levels":             f"L{ls}–L{le}",
                "XP range":           f"{xp_lo:,} – {xp_hi:,}",
                "Scan multiplier":    f"{tier['scan_mult']:.2f}×",
                "Effective cap":      f"{eff_cap:,.0f} $AVA",
                "Raffle multiplier":  f"{tier['raffle_mult']:.1f}×",
            })
        st.dataframe(pd.DataFrame(tier_rows), hide_index=True, use_container_width=True)

    # ================================================================
    # TAB 4 — LEVEL CALCULATOR
    # ================================================================
    with tab_calc:
        render_calculator()


if __name__ == "__main__":
    main()
