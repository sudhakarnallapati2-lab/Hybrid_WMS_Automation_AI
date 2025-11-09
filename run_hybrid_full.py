import os, json, datetime, random, smtplib, requests
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

# --------- Load env ---------
load_dotenv()
SIMULATE = os.getenv("SIMULATE", "1") == "1"

# Power BI, Teams, Email, ServiceNow
POWERBI_PUSH_URL = os.getenv("POWERBI_PUSH_URL","")
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL","")
SMTP_SERVER = os.getenv("SMTP_SERVER","")
SMTP_PORT = int(os.getenv("SMTP_PORT","587"))
SMTP_USER = os.getenv("SMTP_USER","")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD","")
EMAIL_TO = os.getenv("EMAIL_TO","")
SN_INSTANCE = os.getenv("SN_INSTANCE","").rstrip("/")
SN_USER = os.getenv("SN_USER","")
SN_PASSWORD = os.getenv("SN_PASSWORD","")

# Oracle/SaaS (mocked if SIMULATE or empty)
ORACLE_DSN = os.getenv("ORACLE_DSN","host:1521/ORCL")
ORACLE_USER = os.getenv("ORACLE_USER","readonly")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD","readonly")
FUSION_BASE_URL = os.getenv("FUSION_BASE_URL","")
FUSION_USER = os.getenv("FUSION_USER","")
FUSION_PASSWORD = os.getenv("FUSION_PASSWORD","")
FUSION_OAUTH_TOKEN = os.getenv("FUSION_OAUTH_TOKEN","")
OCWMS_BASE_URL = os.getenv("OCWMS_BASE_URL","")
OCWMS_OAUTH_TOKEN = os.getenv("OCWMS_OAUTH_TOKEN","")

# --------- OU Map ---------
def load_ou_map():
    try:
        with open("config/ou_map.json","r",encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"default_backend":"EBS","ous":{}}

OU_MAP = load_ou_map()

# --------- Utils ---------
def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")

def ensure_out():
    Path("out").mkdir(exist_ok=True)

def log(msg):
    print(msg)

# --------- Simulators / Stubs ---------
def ebs_query_stuck_lpn():
    if SIMULATE or True:
        # random 0..3
        return [{"lpn_id": i, "status": "STUCK"} for i in range(random.randint(0,3))]
    # Real db connectivity would go here (cx_Oracle).

def ebs_query_aging_waves():
    if SIMULATE or True:
        return [{"wave_id": 1000+i, "age_hours": 4+i} for i in range(random.randint(0,2))]

def cloud_wms_tasks(status="STUCK"):
    if SIMULATE or not OCWMS_BASE_URL:
        return {"tasks":[{"taskId": f"CW-{1000+i}", "status":"STUCK"} for i in range(random.randint(0,3))]}
    # Real REST call:
    hdr = {"Content-Type":"application/json"}
    if OCWMS_OAUTH_TOKEN: hdr["Authorization"] = f"Bearer {OCWMS_OAUTH_TOKEN}"
    r = requests.get(f"{OCWMS_BASE_URL}/api/tasks", headers=hdr, params={"status":status}, timeout=30)
    r.raise_for_status()
    return r.json()

def fusion_inventory_exceptions(limit=5):
    if SIMULATE or not FUSION_BASE_URL:
        return {"items":[{"exceptionId": f"EX-{i}", "message":"Sim simulated"} for i in range(random.randint(0,2))]}
    hdr = {"Content-Type":"application/json"}
    if FUSION_OAUTH_TOKEN: hdr["Authorization"] = f"Bearer {FUSION_OAUTH_TOKEN}"
    r = requests.get(f"{FUSION_BASE_URL}/fscmRestApi/resources/latest/inventoryExceptions", headers=hdr, params={"limit":limit},
                     auth=(FUSION_USER, FUSION_PASSWORD) if not FUSION_OAUTH_TOKEN else None, timeout=30)
    r.raise_for_status()
    return r.json()

# --------- Integrations ---------
def push_to_powerbi(rows):
    if not POWERBI_PUSH_URL:
        log("Power BI not configured, skipping.")
        return
    resp = requests.post(POWERBI_PUSH_URL, json={"rows": rows}, headers={"Content-Type":"application/json"}, timeout=30)
    log(f"Power BI push: {resp.status_code}")

def send_teams(text):
    if not TEAMS_WEBHOOK_URL:
        log("Teams not configured, skipping.")
        return
    resp = requests.post(TEAMS_WEBHOOK_URL, json={"text":text}, timeout=30)
    log(f"Teams: {resp.status_code}")

def send_email(subject, body):
    if not SMTP_SERVER or not EMAIL_TO:
        log("Email not configured, skipping.")
        return
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER or "wms-bot@localhost"
    msg["To"] = EMAIL_TO
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        try:
            s.starttls()
        except Exception:
            pass
        if SMTP_USER and SMTP_PASSWORD:
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.sendmail(msg["From"], EMAIL_TO.split(","), msg.as_string())
    log("Email sent.")

def create_snow_incident(short_desc, description):
    if not SN_INSTANCE:
        log("ServiceNow not configured, skipping.")
        return {"mock":True,"result":{"sys_id":"MOCKSYSID","number":"INC0009999"}}
    url = f"{SN_INSTANCE}/api/now/table/incident"
    resp = requests.post(url, auth=(SN_USER, SN_PASSWORD),
                         headers={"Content-Type":"application/json"},
                         json={"short_description": short_desc, "description": description, "category":"WMS","impact":"2","urgency":"2"},
                         timeout=30)
    try:
        data = resp.json()
    except Exception:
        data = {"status": resp.status_code, "text": resp.text}
    log(f"ServiceNow: {resp.status_code}")
    return data

# --------- Core ---------
def collect_per_ou():
    run_time = now_iso()
    rows = []
    for ou_name, backend in OU_MAP.get("ous", {}).items():
        stuck_lpn = aging_waves = cloud_tasks = fusion_ex = 0
        snow_id = ""
        snow_no = ""

        if backend == "EBS":
            e1 = ebs_query_stuck_lpn()
            e2 = ebs_query_aging_waves()
            stuck_lpn = len(e1)
            aging_waves = len(e2)
        else:
            c1 = cloud_wms_tasks("STUCK")
            cloud_tasks = len(c1.get("tasks", []))
            f1 = fusion_inventory_exceptions(limit=5)
            fusion_ex = len(f1.get("items", f1))

        total_issues = stuck_lpn + aging_waves + cloud_tasks + fusion_ex

        # Auto-create incident per OU if issues
        if total_issues > 0:
            short = f"[{ou_name}] Hybrid WMS Issues Detected"
            desc = f"OU: {ou_name} | Backend: {backend}\nRun: {run_time}\nTotals: {total_issues}\n" \
                   f"stuck_lpn={stuck_lpn} aging_waves={aging_waves} cloud_tasks={cloud_tasks} fusion_ex={fusion_ex}"
            snow = create_snow_incident(short, desc)
            snow_id = (snow.get("result") or snow).get("sys_id", "")
            snow_no = (snow.get("result") or snow).get("number", "")

        rows.append({
            "run_time": run_time,
            "ou_name": ou_name,
            "backend": backend,
            "stuck_lpn": stuck_lpn,
            "aging_waves": aging_waves,
            "cloud_stuck_tasks": cloud_tasks,
            "fusion_exceptions": fusion_ex,
            "total_issues": total_issues,
            "snow_incident_id": snow_id,
            "snow_incident_number": snow_no
        })
    return rows

def main():
    ensure_out()
    rows = collect_per_ou()
    print(json.dumps(rows, indent=2))

    # Save to file
    with open("out/hybrid_report.json","w",encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    # Compose summary
    summary_lines = [f"{r['ou_name']}: {r['total_issues']} issues" for r in rows]
    summary = "Hybrid WMS Daily Report\n" + "\n".join(summary_lines)

    # Integrations
    push_to_powerbi(rows)
    send_teams(summary)
    send_email("Hybrid WMS Daily Report", summary)

    print("\n✅ Done. Report saved to out/hybrid_report.json")

if __name__ == "__main__":
    main()
