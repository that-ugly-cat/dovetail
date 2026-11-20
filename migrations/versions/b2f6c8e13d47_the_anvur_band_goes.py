"""the ANVUR band goes

The band a journal holds on the Italian evaluation agency's lists matters to
people assessed in Italy and to nobody else. The tool now has several users,
and for nearly all of them it was an empty column in every table and a field
to skip on a form. No journal in production carried a value when this ran.

Revision ID: b2f6c8e13d47
Revises: a9d4e7b21c35
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f6c8e13d47"
down_revision: Union[str, Sequence[str], None] = "a9d4e7b21c35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("anvur_class")


def downgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(sa.Column("anvur_class", sa.String(length=64), nullable=True))
