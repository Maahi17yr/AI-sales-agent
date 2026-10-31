"""One-time trusted operator command; never expose as an HTTP endpoint.

Example (after registering normally and confirming account ownership):
python -m scripts.promote_owner --email owner@example.com \\
  --confirm-email owner@example.com --acknowledge-owner-access

Uses DATABASE_URL if set; otherwise the configured local SQLite database.
Run only from a trusted terminal with the correct database environment.
"""
import argparse

from core.services.owner_access_service import promote_registered_customer
from data.database.models import create_tables


def main():
    parser = argparse.ArgumentParser(description="Promote an existing Naija Bites account to owner.")
    parser.add_argument("--email", required=True, help="Registered account email")
    parser.add_argument("--confirm-email", required=True, help="Repeat the exact account email")
    parser.add_argument("--acknowledge-owner-access", action="store_true",
                        help="Confirm that you are authorised to grant full owner access")
    args = parser.parse_args()
    if args.email.strip().lower() != args.confirm_email.strip().lower() or not args.acknowledge_owner_access:
        parser.error("Matching emails and --acknowledge-owner-access are required.")
    create_tables()  # Apply additive role/audit migrations to existing databases.
    result = promote_registered_customer(args.email)
    print(f"Promoted account #{result['id']} to owner. Existing sessions revoked.")


if __name__ == "__main__":
    main()
