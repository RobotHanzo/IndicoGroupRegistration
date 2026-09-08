"""Per-registration-form configuration."""

from sqlalchemy.dialects.postgresql import JSONB

from indico.core.db import db
from indico.core.db.sqlalchemy import UTCDateTime
from indico.util.date_time import format_datetime
from indico.util.string import format_repr

from indico_group_registration.models import SCHEMA
from indico_group_registration.plans import APPLIES_TO_BASE, parse_plans


#: Bumping this invalidates nothing on its own -- it exists so that a change to
#: the wording an organizer has already published is visible as a different
#: version on every membership recorded after it.
DEFAULT_DISCLAIMER_VERSION = 1


class GroupSettings(db.Model):
    """Group-registration configuration for one registration form.

    A row exists only for forms an organizer has actually configured; absence
    means the feature is off, which is why every lookup goes through
    `get_settings` rather than the relationship.
    """

    __tablename__ = 'group_settings'
    __table_args__ = {'schema': SCHEMA}

    registration_form_id = db.Column(
        db.Integer,
        db.ForeignKey('event_registration.forms.id'),
        primary_key=True
    )
    #: Whether participants may create groups on this form at all
    enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )
    #: The organizer's plan table; see `indico_group_registration.plans`
    plans = db.Column(
        JSONB,
        nullable=False,
        default=list
    )
    #: Whether a discount applies to the base fee only, or to the whole price
    applies_to = db.Column(
        db.String,
        nullable=False,
        default=APPLIES_TO_BASE
    )
    #: When unfilled groups are repriced.  ``None`` means "when registration
    #: closes", resolved at read time so that moving the form's deadline moves
    #: this with it.
    reconciliation_dt = db.Column(
        UTCDateTime,
        nullable=True
    )
    #: Whether members may pay before their group has filled
    allow_early_payment = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )
    #: How many groups one user may lead on this form
    max_groups_per_user = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )
    #: Whether registrations still awaiting moderation count toward the seat
    #: target.  Withdrawn and rejected registrations never count.
    count_pending = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )
    #: Whether losing a member after the group confirmed reprices the rest.
    #: Off by default: nobody should be rebilled over somebody else's
    #: moderation.
    revoke_on_member_loss = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )
    #: The wording participants must accept to choose a group plan
    disclaimer_text = db.Column(
        db.Text,
        nullable=False,
        default=''
    )
    disclaimer_version = db.Column(
        db.Integer,
        nullable=False,
        default=DEFAULT_DISCLAIMER_VERSION
    )

    registration_form = db.relationship(
        'RegistrationForm',
        lazy=True,
        backref=db.backref(
            'group_settings',
            uselist=False,
            lazy=True
        )
    )

    @property
    def parsed_plans(self):
        """The plan table as `Plan` objects, smallest first.

        Raises `PlanError` on stored garbage; callers that render participant
        pages should use `indico_group_registration.util.get_plans`, which
        degrades to an empty list instead.
        """
        return parse_plans(self.plans)

    def get_reconciliation_dt(self):
        """When groups on this form get repriced.

        The same chain as `reconcile.effective_deadline_column`, which is what
        the Celery task actually selects on: the form's own reconciliation
        date, then the date registration closes, then the event's start -- so
        the deadline a reminder quotes is the one the task will act on.
        """
        if self.reconciliation_dt is not None:
            return self.reconciliation_dt
        regform = self.registration_form
        if regform.end_dt is not None:
            return regform.end_dt
        return regform.event.start_dt

    def format_reconciliation_dt(self):
        """The deadline as a reader is told it.

        One wording, because a member can be told the same date twice: the
        reminder quotes it through `{group_deadline}`, and the confirmation
        mail quotes it again when a confirmed group can still fall back to
        forming.  The timezone is named rather than merely applied -- this is
        the one figure in either mail somebody may act on at the last minute.
        """
        event = self.registration_form.event
        return f'{format_datetime(self.get_reconciliation_dt(), timezone=event.tzinfo)} ({event.timezone})'

    def __repr__(self):
        return format_repr(self, 'registration_form_id', 'enabled')
