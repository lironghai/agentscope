# -*- coding: utf-8 -*-
"""Add execution trace table for chat diagnostics.

Revision ID: 0002_execution_traces
Revises: 0001_initial
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_execution_traces"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create execution trace diagnostics table."""
    op.create_table(
        "execution_traces",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("reply_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("execution_traces", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_execution_traces_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_execution_traces_session_reply",
            ["session_id", "reply_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_execution_traces_reply_id"),
            ["reply_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_execution_traces_session_id"),
            ["session_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_execution_traces_status"),
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_execution_traces_updated_at"),
            ["updated_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_execution_traces_user_session",
            ["user_id", "session_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_execution_traces_user_id"),
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop execution trace diagnostics table."""
    with op.batch_alter_table("execution_traces", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_execution_traces_user_id"))
        batch_op.drop_index("ix_execution_traces_user_session")
        batch_op.drop_index(batch_op.f("ix_execution_traces_updated_at"))
        batch_op.drop_index(batch_op.f("ix_execution_traces_status"))
        batch_op.drop_index(batch_op.f("ix_execution_traces_session_id"))
        batch_op.drop_index(batch_op.f("ix_execution_traces_reply_id"))
        batch_op.drop_index("ix_execution_traces_session_reply")
        batch_op.drop_index(batch_op.f("ix_execution_traces_created_at"))
    op.drop_table("execution_traces")
