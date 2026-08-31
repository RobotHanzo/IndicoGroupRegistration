"""Repricing groups that never filled.

This runs once per group, at its form's reconciliation deadline.  It is the
only place a member's price can move upward, and the only place a balance can
come into existence.
"""

from indico.core.db import db
from indico.modules.events import Event
from indico.modules.events.registration.models.forms import RegistrationForm
from indico.util.date_time import now_utc

from indico_group_registration.models.groups import GroupState, RegistrationGroup
from indico_group_registration.models.settings import GroupSettings
from indico_group_registration.notifications import notify_group_short
from indico_group_registration.operations import count_qualifying, lock_group, recount_group
from indico_group_registration.plans import best_plan_for_size
from indico_group_registration.pricing import apply_group_pricing


def effective_deadline_column():
    """The deadline a group is reconciled against, as SQL.

    A form without its own reconciliation date falls back to when registration
    closes, and a form without a closing date falls back to when the event
    starts -- so a group can never sit forming forever because an organizer
    left a date blank.
    """
    return db.func.coalesce(
        GroupSettings.reconciliation_dt,
        RegistrationForm.end_dt,
        Event.start_dt,
    )


def get_due_groups(reference_dt=None):
    """Forming groups whose deadline has passed."""
    reference_dt = reference_dt or now_utc()
    return (RegistrationGroup.query
            .join(RegistrationForm, RegistrationForm.id == RegistrationGroup.registration_form_id)
            .join(Event, Event.id == RegistrationForm.event_id)
            .join(GroupSettings, GroupSettings.registration_form_id == RegistrationForm.id)
            .filter(RegistrationGroup.state == GroupState.forming,
                    GroupSettings.enabled,
                    ~RegistrationForm.is_deleted,
                    ~Event.is_deleted,
                    effective_deadline_column().isnot(None),
                    effective_deadline_column() <= reference_dt)
            .all())


def reconcile_group(group):
    """Reprice one group to whatever it actually qualifies for.

    Returns the group, or ``None`` if there was nothing to do.
    """
    group = lock_group(group)
    if group.state != GroupState.forming:
        # Confirmed groups are settled, short ones already ran, dissolved ones
        # have no rate.  Re-running must never move a settled price.
        return None

    count = count_qualifying(group)
    if count >= group.target_size:
        # It filled after all -- a pending member was approved between the
        # deadline and this run.  Confirm it rather than punishing it.
        return recount_group(group)

    old_prices = {member.id: member.registration.price
                  for member in group.members if member.registration is not None}

    best = best_plan_for_size(group.plans, count)
    group.effective_plan_id = best.id if best is not None else None
    group.state = GroupState.short
    group.reconciled_dt = now_utc()
    db.session.flush()

    apply_group_pricing(group)

    outcomes = {member.id: (old_prices.get(member.id), member.registration.price)
                for member in group.members if member.registration is not None}
    notify_group_short(group, outcomes)
    return group


def reconcile_due_groups(reference_dt=None):
    """Reprice every group whose deadline has passed.

    Each group commits on its own so that one bad group cannot strand the rest.
    Returns the groups that were changed.
    """
    from indico_group_registration.plugin import GroupRegistrationPlugin

    reconciled = []
    for group in get_due_groups(reference_dt):
        try:
            if reconcile_group(group) is not None:
                reconciled.append(group)
                db.session.commit()
            else:
                db.session.rollback()
        except Exception:
            db.session.rollback()
            GroupRegistrationPlugin.logger.exception('Could not reconcile group %s', group.id)
    return reconciled
