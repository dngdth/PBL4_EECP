from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.application.dtos.users import CreateUserInput, UpdateUserInput
from app.application.security import Permission
from app.domain.entities.user import User, UserRole
from app.presentation.api.deps import (
    CreateUserUseCase,
    ListUsersUseCase,
    UpdateUserUseCase,
    actor_name,
    authorize,
)
from app.presentation.schemas.users import (
    CreateUserRequest,
    UpdateUserRequest,
    UserView,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=list[UserView])
def list_users(
    use_case: ListUsersUseCase,
    request: Request,
    role: UserRole | None = None,
    active_only: bool = False,
) -> list[UserView]:
    authorize(request, Permission.MANAGE_USERS)
    return [_user_view(user) for user in use_case(role=role, active_only=active_only)]


@router.post("", response_model=UserView, status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest,
    use_case: CreateUserUseCase,
    request: Request,
) -> UserView:
    authorize(request, Permission.MANAGE_USERS)
    user = use_case(
        CreateUserInput(
            username=body.username,
            password=body.password,
            full_name=body.full_name,
            role=body.role,
        )
    )
    request.app.state.container.security_audit.record(
        action="USER_CREATED",
        actor=actor_name(request, "admin"),
        actor_type="ADMIN",
        resource_type="USER_ACCOUNT",
        resource_id=user.id,
        reason_code="CREATED",
        correlation_id=request.headers.get("x-correlation-id"),
    )
    return _user_view(user)


@router.patch("/{user_id}", response_model=UserView)
def update_user(
    user_id: str,
    body: UpdateUserRequest,
    use_case: UpdateUserUseCase,
    request: Request,
) -> UserView:
    authorize(request, Permission.MANAGE_USERS)
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.user_id == user_id and body.is_active is False:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "cannot deactivate currently signed-in administrator account",
        )
    user = use_case(
        UpdateUserInput(
            user_id=user_id,
            full_name=body.full_name,
            password=body.password,
            is_active=body.is_active,
            role=body.role,
        )
    )
    request.app.state.container.security_audit.record(
        action="USER_UPDATED",
        actor=actor_name(request, "admin"),
        actor_type="ADMIN",
        resource_type="USER_ACCOUNT",
        resource_id=user.id,
        reason_code="UPDATED",
        correlation_id=request.headers.get("x-correlation-id"),
    )
    return _user_view(user)


def _user_view(user: User) -> UserView:
    return UserView(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )

