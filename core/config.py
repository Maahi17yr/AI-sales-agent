
import os
from pathlib import Path
from dotenv import load_dotenv

# ==========================================
# 1. PROJECT PATHS
# ==========================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_PATH = BASE_DIR / "data" / "database" / "smallchops.db"
RAG_DOCUMENTS_PATH = BASE_DIR / "core" / "rag" / "knowledge"

load_dotenv(BASE_DIR / ".env")


# ==========================================
# 2. BUSINESS INFORMATION
# ==========================================

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Naija Bites")
CURRENCY = "NGN"

BUSINESS_DESCRIPTION = (
    "A Nigerian food vendor selling local meals, "
    "soups, swallows, proteins, snacks, and small chops."
)


# ==========================================
# 3. AMAZON BEDROCK CONFIGURATION
# ==========================================

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)

AWS_PROFILE = os.getenv("AWS_PROFILE") or None


# ==========================================
# 4. AI AGENT SETTINGS
# ==========================================

AGENT_TEMPERATURE = 0.3
AGENT_MAX_OUTPUT_TOKENS = 1024

AGENT_NAME = "Naija Bites Sales Assistant"

# Keep responses friendly and conversational.
AGENT_RESPONSE_STYLE = "short, friendly, professional"

# The agent must never place an order
# without explicit customer confirmation.
REQUIRE_ORDER_CONFIRMATION = True


# ==========================================
# 5. DISCOUNT POLICY
# ==========================================

DISCOUNTS_ENABLED = True

# Automatic demo promotion applies to every order's food subtotal.
MIN_ORDER_FOR_DISCOUNT = 0

# Absolute maximum percentage allowed.
MAX_DISCOUNT_PERCENT = 50

# Automatic 50% discount on food; delivery is charged separately.
# Format: minimum subtotal -> discount percentage
BULK_DISCOUNT_TIERS = {0: 50}

# Separate promotional discounts may be
# restricted to once every 30 days.
PROMOTIONAL_DISCOUNT_COOLDOWN_DAYS = 30

# Prevent multiple discounts on one order.
ALLOW_DISCOUNT_STACKING = False

# The agent cannot invent discounts.
ALLOW_AGENT_CUSTOM_DISCOUNTS = False


# ==========================================
# 6. ORDER SETTINGS
# ==========================================

ORDER_STATUSES = (
    "pending_payment",
    "confirmed",
    "preparing",
    "out_for_delivery",
    "delivered",
    "cancelled",
)

DEFAULT_ORDER_STATUS = "pending_payment"

# Check inventory again before order creation.
RECHECK_STOCK_BEFORE_ORDER = True

# Do not allow ordering unavailable products.
ALLOW_OUT_OF_STOCK_ORDERS = False

# Customers can modify carts before confirmation.
ALLOW_CART_UPDATES = True


# ==========================================
# 7. DELIVERY SETTINGS
# ==========================================

DELIVERY_ENABLED = True

# Delivery fees and estimated times come
# from the delivery_zones database table.
USE_DATABASE_DELIVERY_FEES = True

# Delivery address is required for delivery orders.
REQUIRE_DELIVERY_ADDRESS = True


# ==========================================
# 8. PAYMENT SETTINGS
# ==========================================

# Mock payments for this assignment.
PAYMENT_PROVIDER = "paystack_test"

PAYMENT_CURRENCY = CURRENCY

# Never mark payments successful just because
# a customer says they have paid.
REQUIRE_PAYMENT_VERIFICATION = True


# ==========================================
# 9. CUSTOMER SESSION SETTINGS
# ==========================================

# Store cart and conversation session in SQLite.
PERSIST_CONVERSATION_SESSIONS = True

# Maximum number of recent messages to send
# to the model, when session logic is implemented.
MAX_CHAT_HISTORY_MESSAGES = 20


# ==========================================
# 10. OWNER NOTIFICATIONS
# ==========================================

OWNER_NOTIFICATIONS_ENABLED = True

# Simulate owner notifications for now.
OWNER_NOTIFICATION_MODE = "mock"


# ==========================================
# 11. DEVELOPMENT SETTINGS
# ==========================================

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
