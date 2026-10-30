"""Local developer terminal for Naija Bites.

Automatically creates a session for a fixed fictional customer in LOCAL DEVELOPMENT.
This is not authentication. Never use this launcher for real customers or production.
"""
import json
import uuid

from dotenv import load_dotenv

from data.database import repository
from data.database.models import get_connection
from core.services.checkout import CheckoutService


def show_pending_review(session_id):
    """Read the saved, server-calculated summary; never trust model output."""
    with get_connection() as conn:
        row = conn.execute(
            'SELECT cart_data FROM conversation_sessions WHERE session_id = ?',
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    pending = json.loads(row['cart_data']).get('pending_checkout')
    if not isinstance(pending, dict):
        return None
    summary = pending.get('summary')
    if not isinstance(summary, dict) or not isinstance(pending.get('fingerprint'), str):
        return None
    return pending


def review_and_confirm(session_id):
    pending = show_pending_review(session_id)
    if pending is None:
        print('No prepared checkout. Ask the agent to prepare your order first.')
        return
    summary = pending['summary']
    print('\n--- ORDER FOR REVIEW ---')
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print('------------------------')
    print('This is not yet an order. No payment has been made.')
    # The approval token is held by the application, not shown to or requested from the AI.
    decision = input('Type APPROVE to place exactly this order, or anything else to cancel: ').strip()
    if decision != 'APPROVE':
        print('Not approved. Your cart remains unchanged.')
        return
    result = CheckoutService(session_id).confirm_checkout(
        customer_approved=True,
        reviewed_fingerprint=pending['fingerprint'],
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    load_dotenv()
    # LOCAL DEVELOPMENT ONLY: this is a fixed fictional customer, not authentication.
    # Never use this automatic binding in a public or multi-user deployment.
    demo_customer = repository.save_customer(
        phone="DEV-NAIJA-BITES-LOCAL-001",
        name="Naija Bites Test Customer",
    )
    session_id = str(uuid.uuid4())
    repository.bind_session_customer(session_id, demo_customer["id"])
    print("Created a new local test session automatically.")

    # Import after loading .env so Bedrock settings are available.
    from core.intelligence.agent import make_agent
    agent = make_agent(session_id)
    print('Naija Bites local chat ready. Commands: /review, /quit')
    while True:
        try:
            message = input('\nYou: ').strip()
        except (KeyboardInterrupt, EOFError):
            print('\nGoodbye!')
            break
        if not message:
            continue
        if message.lower() == '/quit':
            break
        if message.lower() == '/review':
            review_and_confirm(session_id)
            continue
        try:
            response = agent(message)
            print(f'\nNaija Bites: {response}')
        except Exception as error:
            print(f"Agent request failed: {error}")


if __name__ == '__main__':
    main()
