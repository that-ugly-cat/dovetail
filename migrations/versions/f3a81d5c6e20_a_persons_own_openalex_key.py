"""A person's own OpenAlex key

`app_user.openalex_key_encrypted`, Fernet-encrypted like the Anthropic key
beside it.

OpenAlex gives every free account its own daily budget, and the instance had
one key for everybody: a run started by one person spent what the next person
needed. With a key of their own a user's consultations are charged to a ledger
of their own (`budget_ledger.provider` is `openalex:u<id>`, which needs no
schema change — the column is a string).

It also makes the topic sweep possible on an instance with no key at all.
Anonymous cursor paging answers 401, so without a key stage 2 cannot run;
that was found on 20 Nov 2026 by a run that failed there.

Revision ID: f3a81d5c6e20
Revises: e6b2c40d91a7
Create Date: 2026-11-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a81d5c6e20"
down_revision: Union[str, Sequence[str], None] = "e6b2c40d91a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("openalex_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.drop_column("openalex_key_encrypted")
