"""The group itself: its plan, its code, and its state."""

import secrets
from uuid import uuid4

from sqlalchemy.dialects.postgresql import UUID

from indico.core.db import db
from indico.core.db.sqlalchemy import PyIntEnum, UTCDateTime
from indico.util.date_time import now_utc
from indico.util.enum import RichIntEnum
from indico.util.i18n import L_
from indico.util.string import format_repr

from indico_group_registration.models import SCHEMA
from indico_group_registration.plans import best_plan_for_size, get_plan, parse_plans


#: Deliberately missing I, L, O, 0 and 1 -- codes get read aloud and typed from
#: a phone screen, and those are where the mistakes happen.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8


class GroupState(RichIntEnum):
    __titles__ = [None, L_('Forming'), L_('Confirmed'), L_('Short'), L_('Dissolved')]

    #: Seats are filling.  Members pay the chosen plan's rate.
    forming = 1
    #: The plan's seat count was reached.  The rate is final.
    confirmed = 2
    #: The deadline passed with seats empty.  Repriced; balances may be due.
    short = 3
    #: A manager took the group apart.  Everyone is back on the standard rate.
    dissolved = 4

    @property
    def is_priced_by_plan(self):
        """Whether members of a group in this state get a group rate at all."""
        return self in (GroupState.forming, GroupState.confirmed, GroupState.short)

    @property
    def accepts_members(self):
        return self == GroupState.forming


def generate_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class RegistrationGroup(db.Model):
    """A group of registrations on one registration form, led by a participant."""

    __tablename__ = 'groups'
    __table_args__ = (
        db.UniqueConstraint('registration_form_id', 'code'),
        db.CheckConstraint('target_size > 0', name='positive_target_size'),
        {'schema': SCHEMA},
    )

    id = db.Column(
        db.Integer,
        primary_key=True
    )
    registration_form_id = db.Column(
        db.Integer,
        db.ForeignKey('event_registration.forms.id'),
        index=True,
        nullable=False
    )
    #: The code a participant types to join
    code = db.Column(
        db.String,
        nullable=False
    )
    #: The link a participant follows to join.  Regenerating it revokes every
    #: link already shared.
    join_uuid = db.Column(
        UUID,
        index=True,
        unique=True,
        nullable=False,
        default=lambda: str(uuid4())
    )
    #: The group's display name, chosen by the leader.  User input.
    name = db.Column(
        db.String,
        nullable=False
    )
    #: The leader's registration.  Nullable so that deleting a registration
    #: never cascades into deleting the group -- leadership is transferred
    #: instead.
    leader_registration_id = db.Column(
        db.Integer,
        db.ForeignKey('event_registration.registrations.id'),
        index=True,
        nullable=True
    )
    #: The plan the leader chose
    plan_id = db.Column(
        db.String,
        nullable=False
    )
    #: The plan's seat count, copied at creation so that an organizer editing
    #: the plan table cannot silently retarget groups that are already forming
    target_size = db.Column(
        db.Integer,
        nullable=False
    )
    #: The plan actually being charged.  Equal to `plan_id` until
    #: reconciliation; ``None`` afterwards means "no group plan qualified", i.e.
    #: the standard rate.
    effective_plan_id = db.Column(
        db.String,
        nullable=True
    )
    state = db.Column(
        PyIntEnum(GroupState),
        nullable=False,
        default=GroupState.forming
    )
    created_dt = db.Column(
        UTCDateTime,
        nullable=False,
        default=now_utc
    )
    confirmed_dt = db.Column(
        UTCDateTime,
        nullable=True
    )
    reconciled_dt = db.Column(
        UTCDateTime,
        nullable=True
    )

    registration_form = db.relationship(
        'RegistrationForm',
        lazy=True,
        backref=db.backref(
            'registration_groups',
            lazy='dynamic'
        )
    )
    leader_registration = db.relationship(
        'Registration',
        lazy=True,
        foreign_keys=leader_registration_id
    )

    # `members` is defined by the backref on GroupMember.

    @property
    def event(self):
        return self.registration_form.event

    @property
    def settings(self):
        return self.registration_form.group_settings

    @property
    def plans(self):
        settings = self.settings
        if settings is None:
            return ()
        try:
            return parse_plans(settings.plans)
        except Exception:
            # An organizer can leave the plan table in a state the parser
            # rejects.  Pricing must not explode over it; `pricing` treats an
            # empty plan list as "no discount".
            return ()

    @property
    def chosen_plan(self):
        """What the leader picked."""
        return get_plan(self.plans, self.plan_id)

    @property
    def pricing_plan(self):
        """The plan members are actually charged for right now.

        Before reconciliation this is the chosen plan; after it, whatever the
        group turned out to qualify for.  A dissolved group has none.
        """
        if not self.state.is_priced_by_plan:
            return None
        if self.state == GroupState.short:
            return get_plan(self.plans, self.effective_plan_id)
        return self.chosen_plan

    @property
    def qualifying_members(self):
        """Members whose registration counts toward the seat target."""
        return [member for member in self.members if member.counts_toward_target]

    @property
    def member_count(self):
        return len(self.qualifying_members)

    @property
    def seats_left(self):
        return max(self.target_size - self.member_count, 0)

    @property
    def is_full(self):
        return self.member_count >= self.target_size

    def resolve_shortfall_plan(self):
        """The plan this group qualifies for at its current size.

        ``None`` means it qualifies for no group plan, i.e. the standard rate.
        """
        return best_plan_for_size(self.plans, self.member_count)

    @property
    def formatted_code(self):
        """The code as it should be shown: split in two, because people retype it."""
        half = CODE_LENGTH // 2
        if len(self.code) != CODE_LENGTH:
            return self.code
        return f'{self.code[:half]}-{self.code[half:]}'

    @property
    def locator(self):
        return dict(self.registration_form.locator, group_id=self.id)

    def __repr__(self):
        return format_repr(self, 'id', 'code', 'state', _text=self.name)
