"""Disposable full-stack smoke test: Next.js proxy -> FastAPI -> SQLite.

Requires both servers on localhost (frontend :3000, backend :8000), with
NAIJA_BITES_DATA_DIR set to a temporary, non-production directory.
"""
import os
import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:3000"
API = BASE + "/api/backend"
EMAIL = "smoke-owner@example.test"
PASSWORD = "Fictional-Test-Password-12345"


def require(response, status):
    if response.status_code != status:
        raise AssertionError(f"{response.request.method} {response.request.url}: expected {status}, got {response.status_code}: {response.text[:350]}")
    return response


def main():
    if not os.getenv("NAIJA_BITES_DATA_DIR"):
        raise RuntimeError("Set NAIJA_BITES_DATA_DIR to a disposable directory.")
    if os.getenv("DATABASE_URL"):
        raise RuntimeError("Staging smoke must never use DATABASE_URL.")
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for attempt in range(60):
            try:
                if client.get(API + "/ping").status_code == 200 and client.get(BASE + "/").status_code == 200:
                    break
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Local Next.js / FastAPI servers did not start in time.")

        require(client.post(API + "/auth/register", json={
            "name": "Smoke Owner", "email": EMAIL, "password": PASSWORD,
            "role": "owner",  # Public registration must never honour this field.
        }), 201)
        logged_in = require(client.post(API + "/auth/login", json={
            "email": EMAIL, "password": PASSWORD,
        }), 200).json()
        assert logged_in["customer"]["role"] == "customer"
        require(client.get(API + "/owner/me"), 403)
        require(client.get(API + "/owner/overview"), 403)

        subprocess.run([
            sys.executable, "-m", "scripts.promote_owner",
            "--email", EMAIL, "--confirm-email", EMAIL, "--acknowledge-owner-access",
        ], check=True)
        require(client.get(API + "/auth/me"), 401)
        owner = require(client.post(API + "/auth/login", json={
            "email": EMAIL, "password": PASSWORD,
        }), 200).json()
        assert owner["customer"]["role"] == "owner"
        require(client.get(API + "/owner/me"), 200)
        overview = require(client.get(API + "/owner/overview"), 200).json()
        assert overview["paid_revenue"] == 0
        for endpoint in ("/owner/orders", "/owner/menu", "/owner/customers"):
            require(client.get(API + endpoint), 200)
        require(client.get(BASE + "/owner"), 200)
        print("PASS: same-origin proxy, customer isolation, owner promotion, role routing endpoint, dashboard APIs")


if __name__ == "__main__":
    main()
