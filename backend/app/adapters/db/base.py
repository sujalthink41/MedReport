"""The SQLAlchemy declarative base, and the naming convention that goes with it.

The naming convention below looks like boilerplate. It is the most important thing
in this file, and skipping it causes real pain later.

Without it, Postgres invents names for constraints and indexes -
``profiles_owner_id_fkey1``, or something different on every machine. Alembic then
generates migrations that cannot reliably drop or alter them, and a downgrade fails
in production because the constraint is not called what the migration thinks it is.

With it, every constraint name is derived deterministically from the table and
columns, so Alembic can always find what it needs to change.

Add this on day one. Retrofitting means renaming every constraint in a live database.
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """``created_at``, stamped by the database rather than by Python.

    ``server_default=func.now()`` instead of ``default=datetime.now``: the value
    comes from Postgres, so every row is stamped by one clock. Application servers
    drift, and rows written by two containers milliseconds apart can otherwise appear
    out of order in a query that sorts by time.

    ``DateTime(timezone=True)`` throughout. A naive timestamp column is a bug waiting
    for the first deployment in a different region.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
