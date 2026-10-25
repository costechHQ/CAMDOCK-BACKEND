from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import update

from app.dependencies import get_current_user, require_roles
from app.models import User
from tests.test_auth_api import add_session, add_user, bearer, login_token

ROLES = ("admin", "teacher", "parent")

# Throwaway routes that exist only in these tests.
probe_router = APIRouter(prefix="/_test")


@probe_router.get("/admin")
def admin_only(user: Annotated[User, Depends(require_roles("admin"))]):
    return {"role": user.role}


@probe_router.get("/staff")
def staff_only(user: Annotated[User, Depends(require_roles("admin", "teacher"))]):
    return {"role": user.role}


@probe_router.get("/any")
def any_user(user: Annotated[User, Depends(get_current_user)]):
    return {"role": user.role}


@pytest.fixture
def client(api_client):
    api_client.app.include_router(probe_router)
    return api_client


def tokens_by_role(db, client) -> dict[str, str]:
    for role in ROLES:
        add_user(db, email=f"{role}@camdock.test", role=role)
    return {role: login_token(client, email=f"{role}@camdock.test") for role in ROLES}


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/_test/admin", {"admin"}),
        ("/_test/staff", {"admin", "teacher"}),
        ("/_test/any", {"admin", "teacher", "parent"}),
    ],
)
def test_role_matrix(db, client, path, allowed):
    tokens = tokens_by_role(db, client)

    for role, token in tokens.items():
        response = client.get(path, headers=bearer(token))
        if role in allowed:
            assert response.status_code == 200, role
            assert response.json() == {"role": role}
        else:
            assert response.status_code == 403, role

    anonymous = client.get(path)
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"


def test_forbidden_response_is_generic(db, client):
    tokens = tokens_by_role(db, client)

    response = client.get("/_test/admin", headers=bearer(tokens["parent"]))

    assert response.status_code == 403
    assert "www-authenticate" not in response.headers
    assert response.json() == {
        "error": {
            "code": "forbidden",
            "message": "You do not have permission to perform this action.",
        }
    }


def test_authentication_is_checked_before_role(db, client):
    user_id = add_user(db, email="old@camdock.test", role="admin")
    add_session(db, user_id, "expired", timedelta(hours=2), -timedelta(hours=1))
    add_session(
        db, user_id, "revoked", timedelta(hours=1), timedelta(hours=1), revoked=True
    )
    for token in ("expired", "revoked", "garbage"):
        response = client.get("/_test/admin", headers=bearer(token))
        assert response.status_code == 401, token


def test_disabled_admin_gets_401_not_403(db, client):
    add_user(db, role="admin")
    token = login_token(client)
    db.execute(update(User).values(is_active=False))

    assert client.get("/_test/admin", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("roles", [(), ("superuser",), ("Admin",), ("admin", "root")])
def test_require_roles_rejects_bad_arguments(roles):
    with pytest.raises(ValueError):
        require_roles(*roles)


PUBLIC_ROUTES = {("get", "/api/v1/health"), ("post", "/api/v1/auth/login")}


def test_every_route_requires_authentication_unless_allowlisted(make_client):
    """Uses the OpenAPI schema (public API): routes depending on the bearer scheme
    declare `security`. Routes hidden from the schema are not covered by this check."""
    paths = make_client().app.openapi()["paths"]
    operations = {
        (method, path): operation
        for path, item in paths.items()
        for method, operation in item.items()
    }
    assert operations  # guards against the check passing on an empty app

    open_operations = {key for key, op in operations.items() if not op.get("security")}

    assert open_operations == PUBLIC_ROUTES, (
        "add authentication, or allowlist a public route deliberately"
    )
