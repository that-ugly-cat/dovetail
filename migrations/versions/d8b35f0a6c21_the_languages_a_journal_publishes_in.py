"""the languages a journal publishes in

OpenAlex has no language on a source, so it is read from the journal's works,
one credit a journal, and kept here. NULL is "never looked up".

Revision ID: d8b35f0a6c21
Revises: c7a4d92e5b18
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b35f0a6c21"
down_revision: Union[str, Sequence[str], None] = "c7a4d92e5b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(sa.Column("languages", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("languages")
