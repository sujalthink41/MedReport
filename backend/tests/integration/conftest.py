"""Fixtures for tests that need a real Postgres.

These use **testcontainers**: a throwaway Postgres started for the test session and
destroyed afterwards. Not the docker-compose database, on purpose — tests that share
your development database will eventually delete something you cared about, and they
fail differently on a colleague's machine depending on what is already in there.

Every test here is marked ``integration``. Run only the fast ones with::

    uv run pytest tests/domain tests/application tests/api
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    import subprocess

    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except Exception:  # noqa: BLE001 - any failure means 'no docker', and why does not matter
        return False
    return True


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A disposable Postgres for the whole test session.

    Session-scoped because starting a container takes seconds. Per-test isolation
    comes from rolling back transactions, not from restarting the database.
    """
    if not _docker_available():
        pytest.skip("Docker is not running; integration tests skipped")

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        # testcontainers hands back a psycopg2 URL; our stack is async.
        url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        yield url


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> str:
    """The same database, with every migration applied.

    Schema comes from Alembic, never from ``Base.metadata.create_all()``.

    That distinction matters more than it looks: ``create_all`` builds what the
    models describe *today*, so your tests would pass against a schema that your
    migrations cannot actually produce. Running the migrations means the tests
    exercise the same path production does — including a migration that forgot a
    column.
    """
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)

    import os

    previous = os.environ.get("MEDREPORT_DATABASE_URL")
    os.environ["MEDREPORT_DATABASE_URL"] = postgres_url
    from app.core.config import get_settings

    get_settings.cache_clear()  # settings are cached per process
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("MEDREPORT_DATABASE_URL", None)
        else:
            os.environ["MEDREPORT_DATABASE_URL"] = previous
        get_settings.cache_clear()

    return postgres_url


@pytest.fixture
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(migrated_url)
    yield created
    await created.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work is always rolled back.

    Isolation without restarting anything: the test writes freely, and the
    transaction is discarded at the end, so the next test sees a clean database.
    Orders of magnitude faster than truncating tables between tests.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(bind=connection, expire_on_commit=False) as test_session:
            yield test_session
        # A test that provoked an IntegrityError has already had its transaction
        # invalidated, so rolling back again warns. Check first.
        if transaction.is_active:
            await transaction.rollback()
