"""initial schema

Revision ID: 94019e30371a
Revises:
Create Date: 2026-06-20 22:10:03.518083

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "94019e30371a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evaluation_instructions", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", "code", name="uq_prompts_version_code"),
    )
    op.create_table(
        "responses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("inference_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_id", "id", name="uq_responses_prompt_id_id"),
        sa.UniqueConstraint("prompt_id", "model", name="uq_responses_prompt_model"),
    )
    op.create_table(
        "votes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.Integer(), nullable=False),
        sa.Column("response_a_id", sa.Integer(), nullable=False),
        sa.Column("response_b_id", sa.Integer(), nullable=False),
        sa.Column(
            "winner",
            sa.Enum("a", "b", "tie", "neither", name="winner"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("response_time_s", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("response_a_id <> response_b_id", name="ck_votes_responses_different"),
        sa.ForeignKeyConstraint(
            ["prompt_id", "response_a_id"],
            ["responses.prompt_id", "responses.id"],
            name="fk_votes_response_a",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id", "response_b_id"],
            ["responses.prompt_id", "responses.id"],
            name="fk_votes_response_b",
        ),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_votes_created_at", "votes", ["created_at"], unique=False)
    op.create_index("ix_votes_prompt_id", "votes", ["prompt_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_votes_prompt_id", table_name="votes")
    op.drop_index("ix_votes_created_at", table_name="votes")
    op.drop_table("votes")
    op.drop_table("responses")
    op.drop_table("prompts")
    op.drop_table("categories")
    sa.Enum(name="winner").drop(op.get_bind())
