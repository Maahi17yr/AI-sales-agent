"""Optional Strands integration: register search_knowledge_base in agent tools."""
from strands import tool
from .retriever import search_knowledge_base as retrieve


@tool
def search_knowledge_base(query: str) -> dict:
    """Search fictional Naija Bites policy, delivery, payment, pricing, FAQ and ingredient documents.

    Use for business-policy questions. Do not use for live prices, menu stock, customer orders or actual discount approvals.

    Args:
        query: Customer question or specific business-policy topic.
    """
    return retrieve(query)
