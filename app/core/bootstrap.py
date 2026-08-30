import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.repositories.user_repository import UserRepository
from app.core.config import settings

logger = structlog.get_logger()

async def bootstrap_admins(session: AsyncSession):
    """
    Ensures that the initial admin IDs from settings are granted admin roles.
    This operation is idempotent.
    """
    admin_ids = settings.initial_admin_ids
    if not admin_ids:
        logger.info("bootstrap_admins_skipped", reason="no_admin_ids_provided")
        return

    user_repo = UserRepository(session)
    for admin_id in admin_ids:
        user = await user_repo.get_user(admin_id)
        if user.role != "admin":
            logger.info("granting_admin_role", user_id=admin_id)
            await user_repo.set_role(admin_id, "admin")
