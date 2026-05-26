---
paths: ["**/*.py"]
---

# Python Design Patterns

## Repository Pattern

```python
class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_by_id(self, id: UUID) -> User | None:
        return await self.session.get(User, id)

    async def find_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def create(self, user: UserCreate) -> User:
        db_user = User(**user.model_dump())
        self.session.add(db_user)
        await self.session.flush()
        return db_user
```

## Service Layer

Keep business logic in services, not route handlers:

```python
class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    async def register(self, data: UserCreate) -> User:
        existing = await self.repo.find_by_email(data.email)
        if existing:
            raise ConflictError("Email already registered")
        hashed = hash_password(data.password)
        return await self.repo.create(data, hashed_password=hashed)
```

## Dependency Injection (FastAPI)

```python
async def get_user_service(
    db: AsyncSession = Depends(get_db),
) -> UserService:
    return UserService(UserRepository(db))

@router.post("/users")
async def create_user(
    data: UserCreate,
    service: UserService = Depends(get_user_service),
):
    user = await service.register(data)
    return UserResponse.model_validate(user)
```

## Configuration

- Use Pydantic `BaseSettings` for typed configuration
- Load from environment variables, never hardcode
- Separate dev/staging/prod settings

## Error Handling

- Define domain-specific exception hierarchy
- Centralize in FastAPI exception handlers
- Log full context server-side, return safe messages to clients
