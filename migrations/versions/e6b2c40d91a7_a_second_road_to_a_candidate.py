"""A second road to a candidate, and a sheet that stays the sheet

Two changes that arrived together because the second is how the first gets
measured.

**The citation neighbourhood.** Stage 2 reaches a journal through OpenAlex's
topics, and the topics read a text by its object: a normative paper about a
technology came back, three times in 2026, with the journals that build the
technology. A run can now be given the manuscript's reference list, and asks
where the works it cites are themselves being cited.

- `match_run.reference_dois` — the DOIs found in the list. The neighbourhood is
  computed from them and from nothing else.
- `match_result.neighbourhood` — what ties one venue to that bibliography: how
  many of the references lead to it, how many citation links, against how many
  recent works. Stored beside the scope scores and never merged into them. The
  two new values of `bucket`, `neighbourhood` and `conversation`, need no schema
  change: the column is a string.

**The blind sheet's key.** Scoring a filled sheet rebuilt the shuffle from the
seed, and the shuffle is over the venue table, which grows at every sweep. A
sheet printed one day and scored after another consultation was scored against
different decoys under the same row numbers, without an error. `blind_sheet`
keeps which row was which.

Revision ID: e6b2c40d91a7
Revises: d5a83f1c7b40
Create Date: 2026-11-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6b2c40d91a7"
down_revision: Union[str, Sequence[str], None] = "d5a83f1c7b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_run", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reference_dois", sa.JSON(), nullable=True))
    with op.batch_alter_table("match_result", schema=None) as batch_op:
        batch_op.add_column(sa.Column("neighbourhood", sa.JSON(), nullable=True))
    op.create_table(
        "blind_sheet",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("key", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["match_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seed", name="uq_blind_sheet_run_seed"),
    )
    with op.batch_alter_table("blind_sheet", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_blind_sheet_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("blind_sheet", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_blind_sheet_run_id"))
    op.drop_table("blind_sheet")
    with op.batch_alter_table("match_result", schema=None) as batch_op:
        batch_op.drop_column("neighbourhood")
    with op.batch_alter_table("match_run", schema=None) as batch_op:
        batch_op.drop_column("reference_dois")
