import streamlit as st
import json
import pandas as pd
import os
from datetime import datetime, timedelta
import subprocess
import altair as alt
import time
from io import StringIO, BytesIO
from sklearn.linear_model import LinearRegression
import numpy as np

st.set_page_config(page_title="Hybrid WMS Dashboard", layout="wide")

DATA_FILE = "out/hybrid_report.json"
HISTORY_FILE = "out/history.csv"
REFRESH_SECONDS = 60
PASSWORD = "admin123"  # 🔐 Change this

# ✅ ----- Login Protection -----
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Secure Dashboard Login")
    pwd = st.text_input("Enter Dashboard Password", type="password")
    if st.button("Login"):
        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.success("✅ Access granted")
            st.rerun()
        else:
            st.error("❌ Wrong password")
    st.stop()

# ✅ Auto-refresh via query params
qp = st.query_params
refresh_counter = int(qp.get("r", [0])[0]) if "r" in qp else 0
refresh_counter += 1
st.query_params["r"] = str(refresh_counter)

st.title("🚚 Hybrid WMS Monitoring Dashboard (Live)")
st.write(f"⏳ Auto-refresh every {REFRESH_SECONDS} seconds")

# ✅ ---- Run simulation from dashboard ----
if st.button("▶ Run Simulation Now"):
    st.write("Running simulation...")
    subprocess.run(["python", "run_hybrid_full.py"], shell=True)
    st.success("✅ Simulation completed!")
    st.query_params["r"] = "1"
    st.stop()

# ✅ ---- Load latest data ----
if not os.path.exists(DATA_FILE):
    st.error("No report found. Run run_hybrid_full.py first.")
    st.stop()

with open(DATA_FILE, "r") as f:
    data = json.load(f)

df = pd.DataFrame(data)
df["run_time"] = pd.to_datetime(df["run_time"])

# ✅ ---- Append history if new ----
if not os.path.exists(HISTORY_FILE):
    df.to_csv(HISTORY_FILE, index=False)
else:
    hist = pd.read_csv(HISTORY_FILE)
    hist["run_time"] = pd.to_datetime(hist["run_time"])
    if df["run_time"][0] not in list(hist["run_time"]):
        df.to_csv(HISTORY_FILE, mode="a", header=False, index=False)

# Reload full history
hist = pd.read_csv(HISTORY_FILE)
hist["run_time"] = pd.to_datetime(hist["run_time"])

# ✅ ---- Theme Toggle ----
mode = st.radio("Theme Mode", ["Light", "Dark"])
primary_color = "#222" if mode == "Dark" else "#0066cc"
bg_color = "#111" if mode == "Dark" else "#ffffff"
text_color = "#fff" if mode == "Dark" else "#000"

st.markdown(f"""
<style>
body {{
    background-color: {bg_color};
    color: {text_color};
}}
</style>
""", unsafe_allow_html=True)

# ✅ ---- Data Table ----
st.subheader("📌 Latest WMS Results")
st.dataframe(df)

# ✅ ---- Download buttons ----
st.subheader("⬇ Download Data")
csv_buffer = StringIO()
hist.to_csv(csv_buffer, index=False)
st.download_button("Download CSV", data=csv_buffer.getvalue(), file_name="wms_history.csv", mime="text/csv")

excel_buffer = BytesIO()
with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
    hist.to_excel(writer, index=False, sheet_name="history")
st.download_button("Download Excel", data=excel_buffer.getvalue(), file_name="wms_history.xlsx")

# ✅ ---- OU dropdown ----
ou_list = sorted(df["ou_name"].unique())
selected_ou = st.selectbox("Select OU", ou_list)

df_ou = hist[hist["ou_name"] == selected_ou]

col1, col2 = st.columns(2)

# ✅ ---- Trend chart ----
with col1:
    st.subheader(f"📈 Trend Over Time — {selected_ou}")
    if len(df_ou) > 0:
        chart = alt.Chart(df_ou).mark_line(point=True).encode(
            x="run_time:T",
            y="total_issues:Q",
            tooltip=["run_time", "total_issues"]
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("Run multiple cycles to create trend.")

# ✅ ---- Pie chart ----
with col2:
    st.subheader("🥧 Issue Breakdown (Latest Run)")
    latest = df[df["ou_name"] == selected_ou].iloc[0]
    pie_data = pd.DataFrame({
        "issue_type": ["stuck_lpn", "aging_waves", "cloud_stuck_tasks", "fusion_exceptions"],
        "count": [
            latest["stuck_lpn"],
            latest["aging_waves"],
            latest["cloud_stuck_tasks"],
            latest["fusion_exceptions"]
        ]
    })
    pie = alt.Chart(pie_data).mark_arc().encode(
        theta="count",
        color="issue_type",
        tooltip=["issue_type", "count"]
    )
    st.altair_chart(pie, use_container_width=True)

# ✅ ---- Clickable ServiceNow links ----
st.subheader("🔗 ServiceNow Incident Links")
for _, r in df.iterrows():
    if r["snow_incident_number"]:
        link = f"https://service-now.com/nav_to.do?uri=incident.do?sys_id={r['snow_incident_id']}"
        st.markdown(f"✅ {r['ou_name']} → [{r['snow_incident_number']}]({link})")
    else:
        st.markdown(f"⭕ {r['ou_name']} → No ticket")

# ✅ ---- AI Forecasting ----
st.subheader("🤖 Forecasting (Next 7 Days)")
if len(df_ou) >= 3:
    # Prepare data
    df_ou_sorted = df_ou.sort_values("run_time")
    X = np.array(range(len(df_ou_sorted))).reshape(-1, 1)
    y = df_ou_sorted["total_issues"].values

    model = LinearRegression()
    model.fit(X, y)

    # Predict next 7 days
    future_x = np.array(range(len(df_ou_sorted), len(df_ou_sorted) + 7)).reshape(-1, 1)
    future_y = model.predict(future_x)

    forecast_dates = [datetime.now() + timedelta(days=i) for i in range(7)]
    forecast_df = pd.DataFrame({"run_time": forecast_dates, "predicted_issues": future_y})

    # Chart
    chart_forecast = alt.Chart(forecast_df).mark_line(point=True, color="red").encode(
        x="run_time:T",
        y="predicted_issues:Q",
        tooltip=["run_time", "predicted_issues"]
    )
    st.altair_chart(chart_forecast, use_container_width=True)
else:
    st.info("Forecast needs at least 3 historical runs.")

# ✅ Auto refresh
time.sleep(REFRESH_SECONDS)
st.rerun()
