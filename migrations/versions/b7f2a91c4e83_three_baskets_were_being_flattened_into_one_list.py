"""Three baskets were being flattened into one list

`cut` returns three lists — the shortlist, the excluded venues shown because
fewer than three passed, and the ones with no profile at all — and the CLI has
always printed them under three headers that say what each one means. Persisting
them concatenated the three with a single running counter, so the basket was
discarded one step after being computed.

The effect was visible on the first run after the web form that declares a
journal by hand: *Future of Science and Ethics*, no profile, score 0.0000, came
out as position **13** of a shortlist capped at twelve. Its zero means «I don't
know»; read as the last row of an ordered list it says «worst of the ones we
found». That is exactly the distinction the rest of the tool exists to keep.

**The backfill is a reconstruction, not a record.** The basket was never stored,
so for existing rows it is derived from what was: an `insufficient profile` flag
means unclassifiable, an `excluded` outcome means excluded, anything else is
shortlist. That is how those rows were built, so it recovers them exactly — but
it is derived, and a row hand-edited since would be re-derived rather than read.

Positions are renumbered **within** each basket. Nothing keys on the old value:
`validate.py` computes its own from an in-memory list, and the two templates and
`explain_match` only display it. A number that ordered three incomparable groups
against each other was saying something false, and a per-basket ordinal is what
it always meant.

Revision ID: b7f2a91c4e83
Revises: a3c71e4b9d02
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7f2a91c4e83"
down_revision: Union[str, Sequence[str], None] = "a3c71e4b9d02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("match_result", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("bucket", sa.String(length=16), nullable=False, server_default="shortlist")
        )
        batch_op.create_index(batch_op.f("ix_match_result_bucket"), ["bucket"], unique=False)

    conn = op.get_bind()

    # Derived from the two JSON columns, which is where the evidence survived.
    # `LIKE` rather than a JSON function on purpose: both are stored as text and
    # the markers are unambiguous strings the pipeline writes itself.
    conn.execute(
        sa.text(
            "UPDATE match_result SET bucket = 'unclassifiable' "
            "WHERE flags LIKE '%insufficient profile%'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE match_result SET bucket = 'excluded' "
            "WHERE bucket = 'shortlist' AND excluded_by LIKE '%\"outcome\": \"excluded\"%'"
        )
    )

    # Renumber inside each basket, keeping the order the run recorded.
    rows = conn.execute(
        sa.text("SELECT id, run_id, bucket FROM match_result ORDER BY run_id, position, id")
    ).fetchall()
    seen: dict[tuple, int] = {}
    for row_id, run_id, bucket in rows:
        key = (run_id, bucket)
        seen[key] = seen.get(key, 0) + 1
        conn.execute(
            sa.text("UPDATE match_result SET position = :p WHERE id = :i"),
            {"p": seen[key], "i": row_id},
        )


def downgrade() -> None:
    # The single running counter, restored across the three baskets in the order
    # the pipeline used to write them.
    conn = op.get_bind()
    order = sa.text(
        "SELECT id, run_id FROM match_result "
        "ORDER BY run_id, CASE bucket WHEN 'shortlist' THEN 0 WHEN 'excluded' THEN 1 ELSE 2 END, "
        "position, id"
    )
    seen: dict[int, int] = {}
    for row_id, run_id in conn.execute(order).fetchall():
        seen[run_id] = seen.get(run_id, 0) + 1
        conn.execute(
            sa.text("UPDATE match_result SET position = :p WHERE id = :i"),
            {"p": seen[run_id], "i": row_id},
        )

    with op.batch_alter_table("match_result", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_match_result_bucket"))
        batch_op.drop_column("bucket")
