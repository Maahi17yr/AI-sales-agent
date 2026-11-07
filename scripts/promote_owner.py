"""One-time trusted operator command; never expose as an HTTP endpoint.

Example (after registering normally and confirming account ownership):
python -m scripts.promote_owner --email owner@example.com \\
  --confirm-email owner@example.com --acknowledge-owner-access

Uses DATABASE_URL if set; otherwise the configured local SQLite database.
Run only from a trusted terminal with the correct database environment.
"""
import argparse
import os
from urllib.parse import urlsplit

from core.services.owner_access_service import promote_registered_customer
from data.database.models import create_tables



def validate_database_url(raw: str) -> None:
    """Check the URI shape without opening a connection or exposing its value."""
    value = raw.strip()
    if not value:
        raise ValueError("DATABASE_URL is missing; the configured fallback is local SQLite.")
    if value.lower().startswith("database_url="):
        raise ValueError("Paste only the PostgreSQL URI, without the DATABASE_URL= prefix.")
    if value.startswith(('"', "'")) or value.endswith(('"', "'")):
        raise ValueError("Remove surrounding quotation marks from DATABASE_URL.")
    if value.lower().startswith("psql ") or value.lower() == "psql":
        raise ValueError("Paste the PostgreSQL URI, not a psql command.")
    if any(char.isspace() for char in value):
        raise ValueError("DATABASE_URL contains whitespace; paste a single URI.")
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ("postgresql", "postgres")
                 and bool(parts.hostname)
                 and bool(parts.path.strip("/")))
        # Also reject malformed port values without showing the original URI.
        if parts.port is not None and parts.port == 0:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("DATABASE_URL must be a valid PostgreSQL URI with host and database.")


def main():
    parser = argparse.ArgumentParser(description="Promote an existing Naija Bites account to owner.")
    parser.add_argument("--email", help="Registered account email")
    parser.add_argument("--confirm-email", help="Repeat the exact account email")
    parser.add_argument("--acknowledge-owner-access", action="store_true",
                        help="Confirm that you are authorised to grant full owner access")
    parser.add_argument("--check-config", action="store_true",
                        help="Validate DATABASE_URL format without connecting to a database")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if args.check_config:
        if args.email or args.confirm_email or args.acknowledge_owner_access:
            parser.error("--check-config cannot be combined with promotion options.")
        try:
            validate_database_url(url)
        except ValueError as exc:
            parser.error(str(exc))
        print("DATABASE_URL format is valid; no database connection was attempted.")
        return
    if not args.email or not args.confirm_email or not args.acknowledge_owner_access:
        parser.error("Matching emails and --acknowledge-owner-access are required.")
    if url.strip():
        try:
            validate_database_url(url)
        except ValueError as exc:
            parser.error(str(exc))
    if args.email.strip().lower() != args.confirm_email.strip().lower() or not args.acknowledge_owner_access:
        parser.error("Matching emails and --acknowledge-owner-access are required.")
    create_tables()  # Apply additive role/audit migrations to existing databases.
    result = promote_registered_customer(args.email)
    print(f"Promoted account #{result['id']} to owner. Existing sessions revoked.")


if __name__ == "__main__":
    main()
