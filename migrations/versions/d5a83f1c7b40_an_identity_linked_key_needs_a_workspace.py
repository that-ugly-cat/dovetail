"""An identity-linked key needs a workspace

Found by running it. The first real stage 5a call came back 400 with

    anthropic-workspace-id is required when authenticating with an
    identity-linked API key; send the id of the workspace this request acts in.

Not every Anthropic key is the same kind of thing. A plain API key carries its
own workspace; an **identity-linked** key belongs to a person across several,
and every request has to name which one it acts in. The SDK has no parameter for
it — it goes in `default_headers` — so a key of that kind fails every call until
the header is there.

The workspace id sits beside the key and is **not** encrypted: it is an
identifier, not a secret, and encrypting it would make it unreadable in exactly
the situation where somebody is trying to work out why their key is refused.

Revision ID: d5a83f1c7b40
Revises: c4e19d7b2a06
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5a83f1c7b40"
down_revision: Union[str, Sequence[str], None] = "c4e19d7b2a06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("anthropic_workspace_id", sa.String(length=128), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.drop_column("anthropic_workspace_id")
