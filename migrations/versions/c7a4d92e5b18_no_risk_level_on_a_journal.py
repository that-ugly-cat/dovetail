"""no risk level on a journal

`venue.predatory_risk` held a level and the signals behind it. Two of the four
signals measured nothing about the journal — one was never fed, one recorded a
gap in the source — and the level was only ever computed for the finalists of a
consultation, so it was empty on all but a hundred journals out of twenty
thousand. The tool no longer says how trustworthy a journal is.

Revision ID: c7a4d92e5b18
Revises: b2f6c8e13d47
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7a4d92e5b18"
down_revision: Union[str, Sequence[str], None] = "b2f6c8e13d47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("predatory_risk")


def downgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(sa.Column("predatory_risk", sa.JSON(), nullable=True))
