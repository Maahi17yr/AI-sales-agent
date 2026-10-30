
from data.database.models import get_connection


def reset_sessions():
    """Delete all saved customer carts and chat sessions."""

    confirmation = input(
        "Reset all customer carts and sessions? (yes/no): "
    ).strip().lower()

    if confirmation != "yes":
        print("Reset cancelled.")
        return

    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM conversation_sessions"
        )

        print(
            f"Reset complete. {cursor.rowcount} session(s) deleted."
        )


if __name__ == "__main__":
    reset_sessions()
