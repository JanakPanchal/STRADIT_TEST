"""colleges: onboarded college entities; candidates reference them instead of free text

Existing free-text colleges are migrated into the new table (one row per distinct name) and the
candidates are linked to them, so no data is lost.

Revision ID: 0003
Revises: 0002
"""
import re
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _code(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", "".join(w[0] for w in name.split() if w) or name).upper()[:8] or "COLLEGE"
    code, n = base, 1
    while code in taken:
        n += 1
        code = f"{base}{n}"
    taken.add(code)
    return code


def upgrade() -> None:
    op.create_table(
        "colleges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state", sa.String(100), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("website", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=False),
        sa.Column("contact_phone", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name", name="uq_colleges_name"),
        sa.UniqueConstraint("code", name="uq_colleges_code"),
    )
    with op.batch_alter_table("candidates") as b:
        b.add_column(sa.Column("college_id", sa.Integer(), nullable=True))
        b.create_index("ix_candidates_college_id", ["college_id"])
        b.create_foreign_key("fk_candidates_college", "colleges", ["college_id"], ["id"], ondelete="SET NULL")

    # ---- data: free-text college -> college row
    conn = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    taken: set[str] = set()
    for (name,) in conn.execute(sa.text("SELECT DISTINCT college FROM candidates WHERE college IS NOT NULL AND TRIM(college) <> ''")).fetchall():
        name = name.strip()
        exists = conn.execute(sa.text("SELECT id FROM colleges WHERE lower(name) = lower(:n)"), {"n": name}).first()
        if not exists:
            conn.execute(sa.text(
                "INSERT INTO colleges (name, code, city, state, address, website, contact_name, contact_email, "
                "contact_phone, notes, is_active, created_at) VALUES (:n, :c, '', '', '', '', '', '', '', "
                "'Migrated from candidate records', :a, :t)"), {"n": name, "c": _code(name, taken), "a": True, "t": now})
        conn.execute(sa.text("UPDATE candidates SET college_id = (SELECT id FROM colleges WHERE lower(name) = lower(:n)) "
                             "WHERE TRIM(college) = :n"), {"n": name})

    with op.batch_alter_table("candidates") as b:
        b.drop_column("college")


def downgrade() -> None:
    with op.batch_alter_table("candidates") as b:
        b.add_column(sa.Column("college", sa.String(200), nullable=False, server_default=""))
    op.get_bind().execute(sa.text("UPDATE candidates SET college = COALESCE((SELECT name FROM colleges WHERE colleges.id = candidates.college_id), '')"))
    with op.batch_alter_table("candidates") as b:
        b.drop_constraint("fk_candidates_college", type_="foreignkey")
        b.drop_index("ix_candidates_college_id")
        b.drop_column("college_id")
    op.drop_table("colleges")
