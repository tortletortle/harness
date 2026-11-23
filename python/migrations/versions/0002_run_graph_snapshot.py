"""Add immutable WorkGraph snapshot columns to execution_runs.

Revision ID: 0002_run_graph_snapshot
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_run_graph_snapshot"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("execution_runs")}
    if "graph_snapshot_json" not in columns:
        op.add_column("execution_runs", sa.Column("graph_snapshot_json", sa.Text(), nullable=True))
    if "graph_snapshot_hash" not in columns:
        op.add_column("execution_runs", sa.Column("graph_snapshot_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("execution_runs")}
    if "graph_snapshot_hash" in columns:
        with op.batch_alter_table("execution_runs") as batch_op:
            batch_op.drop_column("graph_snapshot_hash")
    columns = {item["name"] for item in sa.inspect(bind).get_columns("execution_runs")}
    if "graph_snapshot_json" in columns:
        with op.batch_alter_table("execution_runs") as batch_op:
            batch_op.drop_column("graph_snapshot_json")
