"""Quick manual retrieval smoke test: python -m rag.test_retrieval"""
from .ingest import build_index
from .retriever import search_knowledge_base

if __name__ == "__main__":
    print("INDEX:", build_index())
    for q in (
        "Can I get a refund for missing food?",
        "Do you deliver to Ikeja and how much is the fee?",
        "Can you dash me a discount for a bulk order?",
        "What happens if I never pay?",
        "Does egusi contain allergens?",
        "What time is the same-day delivery cut-off?",
    ):
        result = search_knowledge_base(q, limit=3)
        print("\nQUESTION:", q)
        for hit in result["results"]:
            print(" -", hit["source"], "|", hit["heading"])
