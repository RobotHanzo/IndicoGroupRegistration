"""Create the group registration tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-08-31 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from indico.core.db.sqlalchemy import PyIntEnum, UTCDateTime

from indico_group_registration.models.groups import GroupState


revision = 'a1b2c3d4e5f6'
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = 'plugin_group_registration'


def upgrade():
    op.execute(f'CREATE SCHEMA {SCHEMA}')

    op.create_table(
        'group_settings',
        sa.Column('registration_form_id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('plans', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('applies_to', sa.String(), nullable=False, server_default='base'),
        sa.Column('reconciliation_dt', UTCDateTime, nullable=True),
        sa.Column('allow_early_payment', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('max_groups_per_user', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('count_pending', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('revoke_on_member_loss', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('disclaimer_text', sa.Text(), nullable=False, server_default=''),
        sa.Column('disclaimer_version', sa.Integer(), nullable=False, server_default='1'),
        sa.ForeignKeyConstraint(['registration_form_id'], ['event_registration.forms.id']),
        sa.PrimaryKeyConstraint('registration_form_id'),
        schema=SCHEMA,
    )

    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('registration_form_id', sa.Integer(), nullable=False, index=True),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('join_uuid', postgresql.UUID(), nullable=False, index=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('leader_registration_id', sa.Integer(), nullable=True, index=True),
        sa.Column('plan_id', sa.String(), nullable=False),
        sa.Column('target_size', sa.Integer(), nullable=False),
        sa.Column('effective_plan_id', sa.String(), nullable=True),
        sa.Column('state', PyIntEnum(GroupState), nullable=False),
        sa.Column('created_dt', UTCDateTime, nullable=False),
        sa.Column('confirmed_dt', UTCDateTime, nullable=True),
        sa.Column('reconciled_dt', UTCDateTime, nullable=True),
        sa.CheckConstraint('target_size > 0', name='positive_target_size'),
        sa.ForeignKeyConstraint(['registration_form_id'], ['event_registration.forms.id']),
        sa.ForeignKeyConstraint(['leader_registration_id'], ['event_registration.registrations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('registration_form_id', 'code'),
        sa.UniqueConstraint('join_uuid'),
        schema=SCHEMA,
    )

    op.create_table(
        'group_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False, index=True),
        sa.Column('registration_id', sa.Integer(), nullable=False, index=True),
        sa.Column('joined_dt', UTCDateTime, nullable=False),
        sa.Column('applied_amount', sa.Numeric(11, 2), nullable=False, server_default='0'),
        sa.Column('disclaimer_version', sa.Integer(), nullable=True),
        sa.Column('disclaimer_accepted_dt', UTCDateTime, nullable=True),
        sa.ForeignKeyConstraint(['group_id'], [f'{SCHEMA}.groups.id']),
        sa.ForeignKeyConstraint(['registration_id'], ['event_registration.registrations.id']),
        sa.PrimaryKeyConstraint('id'),
        # One group per registration. Everything in the plugin relies on this.
        sa.UniqueConstraint('registration_id'),
        schema=SCHEMA,
    )


def downgrade():
    op.drop_table('group_members', schema=SCHEMA)
    op.drop_table('groups', schema=SCHEMA)
    op.drop_table('group_settings', schema=SCHEMA)
    op.execute(f'DROP SCHEMA {SCHEMA}')
