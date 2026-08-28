"""Stage 5a: the genre judgement

Three columns for the question stages 3 and 4 cannot ask — *does this journal
publish things made like this one?* Scope says what a paper is about; genre says
what shape it is, and an empirical study and a conceptual essay on the same
subject score identically on the first and belong in different journals. It is
the criterion the two desk rejects of 2026 were missing.

- `venue.recent_titles` — what the journal published most recently, which is
  what the judgement reads it against. Cached on the record because it is
  inventory: it costs 10 credits a journal and changes on the timescale of an
  issue, not of a consultation.
- `match_result.genre_verdict` — the answer, stored **beside** the scores and
  never merged into them. The judgement is not reproducible, and ordering a list
  on it would make the list impossible to explain.
- `app_user.anthropic_key_encrypted` — per user, Fernet-encrypted. A judgement
  bills the account of whoever started it, and this box keeps no model
  credential of its own.

Revision ID: c4e19d7b2a06
Revises: b7f2a91c4e83
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e19d7b2a06"
down_revision: Union[str, Sequence[str], None] = "b7f2a91c4e83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(sa.Column("recent_titles", sa.JSON(), nullable=True))
    with op.batch_alter_table("match_result", schema=None) as batch_op:
        batch_op.add_column(sa.Column("genre_verdict", sa.JSON(), nullable=True))
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("anthropic_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.drop_column("anthropic_key_encrypted")
    with op.batch_alter_table("match_result", schema=None) as batch_op:
        batch_op.drop_column("genre_verdict")
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("recent_titles")
