"""A run started from the web answers before it finishes

A consultation launched from the UI redirects to its own page while the sweep is
still going — a hundred-odd calls, half a minute or so. So the row exists with
nothing in it yet, and that state used to be indistinguishable from a run whose
process died halfway. The two want opposite reactions from whoever is looking,
so they get different values.

Existing rows are stamped `done`: every run recorded before this migration came
from the CLI, where the caller waited for it.

Revision ID: a3c71e4b9d02
Revises: f16dd5308040
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c71e4b9d02"
down_revision: Union[str, Sequence[str], None] = "f16dd5308040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_run", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="done")
        )
        batch_op.add_column(sa.Column("error_code", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("error_detail", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("match_run", schema=None) as batch_op:
        batch_op.drop_column("finished_at")
        batch_op.drop_column("error_detail")
        batch_op.drop_column("error_code")
        batch_op.drop_column("status")
