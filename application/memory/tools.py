"""Session-bound Strands tools for optional customer food preferences."""
from strands import tool
from memory import preferences


def make_preference_tools(session_id):
    @tool
    def get_customer_preferences() -> dict:
        """Retrieve food preferences previously saved by this verified customer."""
        try:
            return {"success": True, "preferences": preferences.get_preferences(session_id)}
        except (ValueError, PermissionError) as exc:
            return {"success": False, "message": str(exc)}

    @tool
    def remember_food_preference(key: str, value: str) -> dict:
        """Prepare a food preference for trusted UI review. This does NOT save it.

        Args:
            key: One of spice_level, favorite_food, disliked_food, dietary_preference, food_allergy, other_food_preference.
            value: The customer's own preference, never inferred or fabricated.
        """
        try:
            pending = preferences.request_preference(session_id, key, value)
            return {"success": True, "pending_approval": True, "proposal": pending, "message": "Preference prepared, not saved. Ask the customer to approve in the memory review card."}
        except (ValueError, PermissionError) as exc:
            return {"success": False, "message": str(exc)}

    @tool
    def forget_food_preference(key: str) -> dict:
        """Delete a saved preference when the customer asks to forget it.

        Args:
            key: Preference category to delete.
        """
        try:
            return {"success": True, "deleted": preferences.forget_preference(session_id, key)}
        except (ValueError, PermissionError) as exc:
            return {"success": False, "message": str(exc)}

    return [get_customer_preferences, remember_food_preference, forget_food_preference]
