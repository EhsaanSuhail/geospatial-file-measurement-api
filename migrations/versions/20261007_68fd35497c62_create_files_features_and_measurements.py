"""create files, features and measurements

Revision ID: 68fd35497c62
Revises:
Create Date: 2026-10-07 19:07:35.140662

Enumerations are VARCHAR columns guarded by named CHECK constraints (see
app.db.base.enum_column_type). Child rows are removed with their parent (ON DELETE CASCADE).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "68fd35497c62"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=32), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("original_crs", sa.Text(), nullable=True),
        sa.Column("feature_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
        sa.CheckConstraint(
            "file_format IN ('KML', 'SHAPEFILE')", name=op.f("ck_files_file_format")
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'COMPLETED', 'FAILED')", name=op.f("ck_files_file_status")
        ),
        sa.CheckConstraint(
            "file_size_bytes >= 0", name=op.f("ck_files_file_size_bytes_non_negative")
        ),
        sa.CheckConstraint("feature_count >= 0", name=op.f("ck_files_feature_count_non_negative")),
    )

    op.create_table(
        "features",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("feature_index", sa.Integer(), nullable=False),
        sa.Column("layer_name", sa.Text(), nullable=False),
        sa.Column("geometry_type", sa.String(length=32), nullable=True),
        sa.Column("geometry", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("geometry_error", sa.Text(), nullable=True),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_features")),
        sa.ForeignKeyConstraint(
            ["file_id"], ["files.id"], name=op.f("fk_features_file_id_files"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "file_id", "feature_index", name=op.f("uq_features_file_id_feature_index")
        ),
        sa.CheckConstraint(
            "feature_index >= 0", name=op.f("ck_features_feature_index_non_negative")
        ),
    )

    op.create_table(
        "measurements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feature_id", sa.Uuid(), nullable=False),
        sa.Column("measurement_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("measurement_crs", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_measurements")),
        sa.ForeignKeyConstraint(
            ["feature_id"],
            ["features.id"],
            name=op.f("fk_measurements_feature_id_features"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "feature_id",
            "measurement_type",
            name=op.f("uq_measurements_feature_id_measurement_type"),
        ),
        sa.CheckConstraint(
            "measurement_type IN ('AREA', 'LENGTH', 'NONE')",
            name=op.f("ck_measurements_measurement_type"),
        ),
        sa.CheckConstraint(
            "status IN ('CALCULATED', 'NOT_APPLICABLE', 'UNSUPPORTED', 'FAILED')",
            name=op.f("ck_measurements_measurement_status"),
        ),
        sa.CheckConstraint(
            "(status = 'CALCULATED') = (value IS NOT NULL)",
            name=op.f("ck_measurements_value_present_iff_calculated"),
        ),
        sa.CheckConstraint("value >= 0", name=op.f("ck_measurements_value_non_negative")),
    )


def downgrade() -> None:
    op.drop_table("measurements")
    op.drop_table("features")
    op.drop_table("files")
