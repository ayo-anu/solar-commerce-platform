"""Shared empty database metadata and deterministic object naming."""

from typing import Final

from sqlalchemy import MetaData

DATABASE_NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": (
        "fk_%(table_name)s_%(column_0_N_name)s_"
        "%(referred_table_name)s_%(referred_column_0_N_name)s"
    ),
    "pk": "pk_%(table_name)s",
}

database_metadata = MetaData(naming_convention=DATABASE_NAMING_CONVENTION)
