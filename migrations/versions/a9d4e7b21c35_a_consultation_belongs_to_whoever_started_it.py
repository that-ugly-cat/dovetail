"""A consultation belongs to whoever started it

`match_run.user_id`. Journals stay shared; a consultation is its owner's, and
is read by nobody else — see `dovetail/ownership.py`. Until this, any signed-in
user could read every consultation, title of the manuscript included.

**The existing rows.** A consultation recorded before this has no owner, and a
consultation with no owner is the instance's: admins see it. Where the instance
has exactly one account, every existing consultation is given to it, because
there is nobody else it could have been. Where there are several, the rows are
left ownerless rather than guessed at — handing one person another person's
manuscript is the mistake this column exists to prevent.

Revision ID: a9d4e7b21c35
Revises: f3a81d5c6e20
Create Date: 2026-11-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9d4e7b21c35"
down_revision: Union[str, Sequence[str], None] = "f3a81d5c6e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_match_run_user_id"), ["user_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_match_run_user_id_app_user", "app_user", ["user_id"], ["id"]
        )

    bind = op.get_bind()
    users = bind.execute(sa.text("select id from app_user order by id")).fetchall()
    if len(users) == 1:
        bind.execute(
            sa.text("update match_run set user_id = :uid where user_id is null"),
            {"uid": users[0][0]},
        )


def downgrade() -> None:
    with op.batch_alter_table("match_run", schema=None) as batch_op:
        batch_op.drop_constraint("fk_match_run_user_id_app_user", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_match_run_user_id"))
        batch_op.drop_column("user_id")
