"""Unit tests for deterministic database object naming."""

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    UniqueConstraint,
)

from solar_platform.database_metadata import (
    DATABASE_NAMING_CONVENTION,
    database_metadata,
)

pytestmark = pytest.mark.unit


def test_production_metadata_is_empty_and_has_exact_naming_convention() -> None:
    assert not database_metadata.tables
    assert dict(database_metadata.naming_convention) == {
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": (
            "fk_%(table_name)s_%(column_0_N_name)s_"
            "%(referred_table_name)s_%(referred_column_0_N_name)s"
        ),
        "pk": "pk_%(table_name)s",
    }


def _named_objects() -> tuple[str, str, str, str, str]:
    metadata = MetaData(naming_convention=DATABASE_NAMING_CONVENTION)
    parent = Table(
        "parent",
        metadata,
        Column("tenant_id", Integer, primary_key=True),
        Column("record_id", Integer, primary_key=True),
    )
    child = Table(
        "child",
        metadata,
        Column("tenant_id", Integer, nullable=False),
        Column("record_id", Integer, nullable=False),
        Column("sequence", Integer, nullable=False),
        UniqueConstraint("tenant_id", "sequence"),
        CheckConstraint("sequence > 0", name="positive_sequence"),
        ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["parent.tenant_id", "parent.record_id"],
        ),
    )
    index = Index(None, child.c.tenant_id, child.c.sequence)
    primary_key = parent.primary_key
    unique = next(
        constraint
        for constraint in child.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    check = next(
        constraint
        for constraint in child.constraints
        if isinstance(constraint, CheckConstraint)
    )
    foreign_key = next(
        constraint
        for constraint in child.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    return (
        str(primary_key.name),
        str(unique.name),
        str(check.name),
        str(foreign_key.name),
        str(index.name),
    )


def test_composite_constraint_and_index_names_are_deterministic() -> None:
    expected = (
        "pk_parent",
        "uq_child_tenant_id_sequence",
        "ck_child_positive_sequence",
        "fk_child_tenant_id_record_id_parent_tenant_id_record_id",
        "ix_child_tenant_id_sequence",
    )

    assert _named_objects() == expected
    assert _named_objects() == expected
