"""One registration's membership of one group."""

from decimal import Decimal

from indico.core.db import db
from indico.core.db.sqlalchemy import UTCDateTime
from indico.modules.events.registration.models.registrations import RegistrationState
from indico.util.date_time import now_utc
from indico.util.string import format_repr

from indico_group_registration.models import SCHEMA


#: Registrations in these states never count toward a seat target and never
#: get a group rate.
DEAD_STATES = frozenset({RegistrationState.rejected, RegistrationState.withdrawn})


class GroupMember(db.Model):
    """Links a registration to its group, and records what it was charged.

    The unique constraint on ``registration_id`` is what enforces "one group
    per registration"; everything else in the plugin relies on it.
    """

    __tablename__ = 'group_members'
    __table_args__ = {'schema': SCHEMA}

    id = db.Column(
        db.Integer,
        primary_key=True
    )
    group_id = db.Column(
        db.Integer,
        db.ForeignKey(f'{SCHEMA}.groups.id'),
        index=True,
        nullable=False
    )
    registration_id = db.Column(
        db.Integer,
        db.ForeignKey('event_registration.registrations.id'),
        index=True,
        unique=True,
        nullable=False
    )
    joined_dt = db.Column(
        UTCDateTime,
        nullable=False,
        default=now_utc
    )
    #: The discount currently written onto this registration, as a negative
    #: amount.  Mirrors the field data so that the balances view can be built
    #: with one query instead of unpacking every registration's JSON.
    applied_amount = db.Column(
        db.Numeric(11, 2),
        nullable=False,
        default=0
    )
    #: Which version of the disclaimer this person accepted, and when.  This is
    #: the record that makes "entry may be refused" enforceable.
    disclaimer_version = db.Column(
        db.Integer,
        nullable=True
    )
    disclaimer_accepted_dt = db.Column(
        UTCDateTime,
        nullable=True
    )

    group = db.relationship(
        'RegistrationGroup',
        lazy=True,
        backref=db.backref(
            'members',
            lazy=True,
            cascade='all, delete-orphan',
            order_by='GroupMember.joined_dt'
        )
    )
    registration = db.relationship(
        'Registration',
        lazy=True,
        backref=db.backref(
            'group_membership',
            uselist=False,
            lazy=True
        )
    )

    @property
    def counts_toward_target(self):
        """Whether this member fills a seat."""
        registration = self.registration
        if registration is None or registration.is_deleted or registration.state in DEAD_STATES:
            return False
        if registration.state == RegistrationState.pending:
            settings = self.group.settings
            return settings.count_pending if settings else True
        return True

    @property
    def paid_amount(self):
        """What this member has actually handed over, as a positive amount."""
        registration = self.registration
        if registration is None or not registration.is_paid or registration.transaction is None:
            return Decimal(0)
        return Decimal(str(registration.transaction.amount))

    @property
    def balance_due(self):
        """What the member still owes, positive when they are behind.

        Non-zero after a group is repriced upward: their transaction is still
        successful so Indico considers them settled, and only we know better.
        """
        registration = self.registration
        if registration is None or not registration.is_paid:
            return Decimal(0)
        return max(registration.price - self.paid_amount, Decimal(0))

    def __repr__(self):
        return format_repr(self, 'id', 'group_id', 'registration_id', 'applied_amount')
