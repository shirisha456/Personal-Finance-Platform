from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError


async def get_owned[T](
    db: AsyncSession,
    model: type[T],
    id_: UUID,
    user_id: UUID,
    *,
    user_id_attr: str = "user_id",
) -> T:
    """Fetch a row by primary key and confirm it belongs to `user_id`.

    Centralized in one place rather than hand-rolled per router: a
    resource that doesn't exist and a resource that belongs to someone
    else return the identical 404 — never distinguish "doesn't exist"
    from "belongs to someone else" in the response, so every module
    gets that guarantee for free instead of re-implementing (and
    potentially getting wrong) the same check.
    """
    obj = await db.get(model, id_)
    if obj is None or getattr(obj, user_id_attr) != user_id:
        raise NotFoundError(f"{model.__name__} not found.")
    return obj
