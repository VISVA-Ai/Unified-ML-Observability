"""
Unified ML Observability Dashboard
===================================
A Streamlit-based dashboard surfacing real-time telemetry, model performance
metrics, and active incident alerts — all served through the Control API.
"""

import os
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Page config & Custom CSS for Premium Dark/Glassmorphism Look
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Unified ML Observability",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    /* Global Background and Fonts */
    .stApp {
        background-color: #0E1117;
        font-family: 'Inter', sans-serif;
    }
    
    /* Hide top header bar for a cleaner look */
    header {visibility: hidden;}
    
    /* Premium Metric Cards */
    div[data-testid="metric-container"] {
        background-color: #1E1E24;
        border: 1px solid #2B2C36;
        padding: 20px 24px;
        border-radius: 12px;
        box-shadow: 0 8px 16px rgba(0,0,0,0.4);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    div[data-testid="metric-container"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 12px 24px rgba(0,0,0,0.6);
        border-color: #00CC96;
    }
    
    /* Metric Card Labels */
    div[data-testid="metric-container"] label {
        color: #A0AEC0;
        font-weight: 500;
        font-size: 0.9rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    
    /* Metric Card Values */
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        color: #FFFFFF;
        font-size: 2.2rem;
        font-weight: 700;
        padding-top: 5px;
    }
    
    /* Main Headers */
    h2, h3 {
        color: #E2E8F0;
        font-weight: 600;
        letter-spacing: -0.02em;
    }
    
    /* Dataframe Styling */
    div[data-testid="stDataFrame"] {
        background-color: #1E1E24;
        border-radius: 12px;
        border: 1px solid #2B2C36;
        padding: 5px;
    }
    
    /* Refresh Button */
    .stButton>button {
        background: linear-gradient(135deg, #00CC96 0%, #009970 100%);
        color: white;
        border: none;
        border-radius: 6px;
        padding: 0.5rem 1rem;
        font-weight: 600;
        box-shadow: 0 4px 6px rgba(0, 204, 150, 0.2);
        transition: all 0.2s ease;
    }
    .stButton>button:hover {
        box-shadow: 0 6px 12px rgba(0, 204, 150, 0.4);
        transform: translateY(-1px);
    }
    
    /* Custom hr */
    hr {
        border-color: #2B2C36;
        margin: 2rem 0;
    }
</style>
""", unsafe_allow_html=True)

CONTROL_API_URL = os.getenv("CONTROL_API_URL", "http://control_api:8001")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch(path: str, fallback=None):
    """GET from the Control API; return fallback on any error."""
    try:
        resp = requests.get(f"{CONTROL_API_URL}{path}", timeout=3)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return fallback


def metric_card(label: str, value, delta=None, help_text: str = ""):
    """Render a single metric with optional delta."""
    st.metric(label=label, value=value, delta=delta, help=help_text)

# Common Plotly Layout for Dark Theme
def apply_dark_theme(fig):
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#A0AEC0'),
        margin=dict(t=30, b=30, l=10, r=10),
        xaxis=dict(showgrid=False, zeroline=False, linecolor='#2B2C36'),
        yaxis=dict(showgrid=True, gridcolor='#2B2C36', zeroline=False, linecolor='#2B2C36'),
        legend=dict(bgcolor='rgba(0,0,0,0)', font=dict(color='#A0AEC0'))
    )
    return fig

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("## Unified ML Observability")
st.caption("Fraud Detection Model · `xgboost-fraud-v1.0` · Auto-refreshes on button click")
st.markdown("<br>", unsafe_allow_html=True)

if st.button("Refresh Dashboard", use_container_width=False):
    st.rerun()

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Section 1 — KPI Cards (via Control API /metrics/performance)
# ---------------------------------------------------------------------------
st.subheader("Model Performance (Last Hour)")

perf = fetch("/metrics/performance", fallback={})

col1, col2, col3, col4 = st.columns(4)
with col1:
    metric_card(
        "Total Predictions",
        f"{perf.get('total_predictions', '—'):,}" if perf else "—",
        help_text="Predictions made in the last hour.",
    )
with col2:
    labeled = perf.get("labeled_count", 0)
    coverage = perf.get("label_coverage_pct", 0)
    metric_card(
        "Labels Received",
        f"{labeled:,}" if perf else "—",
        delta=f"{coverage}% coverage" if perf else None,
        help_text="Ground truth labels joined via correlation ID.",
    )
with col3:
    acc = perf.get("accuracy")
    metric_card(
        "Accuracy",
        f"{acc * 100:.1f}%" if acc is not None else "Pending labels…",
        help_text="Calculated on the labeled subset. More labels arrive over time.",
    )
with col4:
    avg_lat = perf.get("avg_latency_ms", 0)
    metric_card(
        "Avg Latency",
        f"{avg_lat:.1f} ms" if perf else "—",
        delta="⚠ SPIKE" if avg_lat and avg_lat > 50 else "OK",
        help_text="Average end-to-end inference latency.",
    )

if not perf:
    st.info("Control API is not reachable yet, or no traffic has been sent. Run `python scripts/load_gen.py` to start.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 2 — Live Telemetry Charts (direct ClickHouse query via shared lib)
# ---------------------------------------------------------------------------
st.subheader("Live Telemetry")

try:
    from shared.db_clickhouse import get_clickhouse_client

    ch = get_clickhouse_client()
    res = ch.query(
        """
        SELECT timestamp, latency_ms, prediction_score, prediction_class, segment
        FROM predictions
        ORDER BY timestamp DESC
        LIMIT 2000
        """
    )

    if res.result_rows:
        df = pd.DataFrame(
            res.result_rows,
            columns=["timestamp", "latency_ms", "prediction_score", "prediction_class", "segment"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["label"] = df["prediction_class"].map({1: "Fraud", 0: "Legitimate"})

        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.markdown("**Inference Latency over Time**")
            fig_lat = px.scatter(
                df,
                x="timestamp",
                y="latency_ms",
                color="label",
                color_discrete_map={"Fraud": "#EF553B", "Legitimate": "#00CC96"},
                opacity=0.8,
                labels={"latency_ms": "Latency (ms)", "timestamp": ""},
            )
            fig_lat = apply_dark_theme(fig_lat)
            fig_lat.update_layout(height=340)
            st.plotly_chart(fig_lat, use_container_width=True)

        with chart_col2:
            st.markdown("**Risk Score Distribution**")
            fig_hist = px.histogram(
                df,
                x="prediction_score",
                color="label",
                nbins=30,
                barmode="overlay",
                color_discrete_map={"Fraud": "#EF553B", "Legitimate": "#00CC96"},
                opacity=0.85,
                labels={"prediction_score": "Risk Score", "count": "Count"},
            )
            fig_hist.add_vline(x=0.6, line_dash="dash", line_color="#F6AD55",
                               annotation_text="Decision threshold (0.6)")
            fig_hist = apply_dark_theme(fig_hist)
            fig_hist.update_layout(height=340)
            st.plotly_chart(fig_hist, use_container_width=True)

        # Prediction volume over time (1-minute buckets)
        st.markdown("**Prediction Volume (1-min buckets)**")
        df["minute"] = df["timestamp"].dt.floor("1min")
        vol_df = (
            df.groupby(["minute", "label"]).size().reset_index(name="count")
        )
        fig_vol = px.bar(
            vol_df,
            x="minute",
            y="count",
            color="label",
            color_discrete_map={"Fraud": "#EF553B", "Legitimate": "#00CC96"},
            labels={"minute": "Time", "count": "Predictions"},
        )
        fig_vol = apply_dark_theme(fig_vol)
        fig_vol.update_layout(height=300, bargap=0.15)
        st.plotly_chart(fig_vol, use_container_width=True)

    else:
        st.info("No telemetry data yet. Run `python scripts/load_gen.py` to generate traffic.")

except Exception as e:
    st.warning(f"Could not connect to ClickHouse: {e}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Section 3 — Active Incidents & Drift Alerts
# ---------------------------------------------------------------------------
st.subheader("Active Incidents & Alerts")

alerts_data = fetch("/alerts?limit=30", fallback=None)

if alerts_data is not None:
    alerts = alerts_data.get("alerts", [])
    if alerts:
        df_alerts = pd.DataFrame(alerts)
        df_alerts["triggered_at"] = pd.to_datetime(df_alerts["triggered_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")

        # Colour-code by severity
        def _severity_color(val):
            colors = {"CRITICAL": "color: #EF553B; font-weight: bold",
                      "WARNING": "color: #F6AD55; font-weight: bold"}
            return colors.get(val, "")

        cols_to_show = [c for c in ["triggered_at", "severity", "alert_type", "message", "drift_score", "model_version"] if c in df_alerts.columns]
        st.dataframe(
            df_alerts[cols_to_show].style.map(_severity_color, subset=["severity"]),
            use_container_width=True,
            height=350,
        )
    else:
        st.success("No active alerts.")
else:
    st.error(f"Could not reach Control API at `{CONTROL_API_URL}`. Ensure all services are running.")

# ---------------------------------------------------------------------------
# Sidebar — Service links
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Service Endpoints")
    st.markdown(f"- [Prediction API Docs](http://localhost:8000/docs)")
    st.markdown(f"- [Control API Docs](http://localhost:8001/docs)")
    st.markdown("---")
    st.markdown("### PSI Drift Thresholds")
    st.markdown("| PSI | Severity |\n|---|---|\n| < 0.10 | OK |\n| 0.10–0.20 | WARNING |\n| > 0.20 | CRITICAL |")
