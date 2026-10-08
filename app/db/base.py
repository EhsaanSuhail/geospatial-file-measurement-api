from enum import StrEnum

from sqlalchemy import Enum, MetaData
from sqlalchemy.orm import DeclarativeBase

# Fixed constraint names keep Alembic migrations predictable and easy to review.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def enum_column_type(enum_class: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint rather than a native PostgreSQL ENUM.

    Adding a value then needs only a constraint change, not `ALTER TYPE ... ADD VALUE`
    (which cannot be rolled back inside a migration transaction).
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )
