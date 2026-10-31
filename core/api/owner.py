"""Owner-only API routes. Customer-facing authentication is shared, permissions are not."""
from fastapi import APIRouter, HTTPException, Request

from core.services import auth_service

router = APIRouter(prefix="/owner", tags=["owner"])


def require_owner(request: Request) -> dict:
    account = auth_service.authenticate_token(request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    if account is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if account["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required.")
    return account


@router.get("/me")
def owner_me(request: Request):
    """Minimal protected endpoint; management data will be added in later phases."""
    owner = require_owner(request)
    return {"owner": owner}
