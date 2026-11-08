"""Secure, opt-in Locust web dashboard for the disposable Naija Bites lab."""
import os
import sys

LAB_URL = "https://naija-bites-live-lab-temp-20261108.onrender.com"
if os.getenv("NAIJA_LOAD_TEST_ISOLATED") != "1":
    raise RuntimeError("Isolated load test opt-in is required")
if os.getenv("LAB_TARGET_URL") != LAB_URL:
    raise RuntimeError("Load test target is not the exact disposable lab")
user = os.environ["LOCUST_DASHBOARD_USER"]
password = os.environ["LOCUST_DASHBOARD_PASSWORD"]
if not user.isalnum() or len(password) < 20 or ":" in password:
    raise RuntimeError("Strong dashboard authentication is required")
if not os.environ.get("LAB_ACCESS_CODE"):
    raise RuntimeError("Fictional lab access code is required")
if any(os.getenv(k) for k in ("LOCUST_HEADLESS", "LOCUST_AUTOSTART")):
    raise RuntimeError("Load testing must start manually from the dashboard")

from locust.main import main
sys.argv = [
    "locust",
    "-f", "scripts/locust_live_lab.py",
    "--web-host", "0.0.0.0",
    "--web-port", str(int(os.getenv("PORT", "10000"))),
    "--web-auth", user + ":" + password,
    "--host", LAB_URL,
]
if __name__ == "__main__":
    main()
