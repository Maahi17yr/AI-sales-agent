
"""Naija Bites Strands AI agent factory.

Creates an agent for a verified customer session.

The AI can browse products, manage carts, and prepare
checkout summaries, but cannot approve orders or payments.
"""

import os

from strands import Agent, tool
from strands.models.bedrock import BedrockModel

from system_prompt import SYSTEM_PROMPT
from database import repository
from tools.cart import make_cart_tools
from tools.orders import make_order_tools
from rag.tool import search_knowledge_base
from memory.session import make_session_manager
from memory.tools import make_preference_tools



def make_agent(session_id: str) -> Agent:
    """Create an AI agent for a trusted customer session."""

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A trusted session ID is required.")

    session = repository.get_session(session_id)

    if not session or session.get("customer_id") is None:
        raise ValueError(
            "Verify and bind a customer to the session first."
        )

    # ---------------------------------------
    # MENU BROWSING
    # ---------------------------------------

    @tool
    def browse_menu(category: str = "") -> dict:
        """Browse actual menu products with available stock.

        Args:
            category: Optional exact category, or empty for all.
        """

        products = repository.get_orderable_products(
            category=category
        )

        return {
            "success": True,
            "products": products,
            "message": (
                "Only products with at least one in-stock "
                "variant are listed. Stock can change."
            ),
        }

    # ---------------------------------------
    # MENU SEARCH
    # ---------------------------------------

    @tool
    def search_menu(query: str) -> dict:
        """Search actual in-stock products by name.

        Args:
            query: Product name to search for.
        """

        if not isinstance(query, str) or not query.strip():
            return {
                "success": False,
                "message": "Please provide a search term.",
            }

        products = repository.get_orderable_products(
            query=query
        )

        return {
            "success": True,
            "products": products,
            "message": (
                "Matching in-stock menu products."
                if products
                else "No matching in-stock menu products."
            ),
        }

    # ---------------------------------------
    # DELIVERY AREAS
    # ---------------------------------------

    @tool
    def list_delivery_areas() -> dict:
        """List supported delivery areas, fees and estimates."""

        return {
            "success": True,
            "areas": repository.get_all_delivery_zones(),
        }

    # ---------------------------------------
    # BEDROCK MODEL
    # ---------------------------------------

    region = os.getenv(
        "AWS_REGION",
        os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )

    model_id = os.getenv("BEDROCK_MODEL_ID")

    if not model_id:
        raise ValueError(
            "Set BEDROCK_MODEL_ID in your environment."
        )

    model = BedrockModel(
        model_id=model_id,
        region_name=region,
    )

    # ---------------------------------------
    # REGISTER AGENT TOOLS
    # ---------------------------------------

    tools = [
        browse_menu,
        search_menu,
        list_delivery_areas,
        search_knowledge_base,
    ]

    tools.extend(make_cart_tools(session_id))
    tools.extend(make_order_tools(session_id))
    tools.extend(make_preference_tools(session_id))

    # ---------------------------------------
    # CREATE AGENT
    # ---------------------------------------

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        session_manager=make_session_manager(session_id),
    )
