"""Writing the discount onto registrations.

The price a member pays is fixed by their group's *pricing plan*, not by how
full the group happens to be.  That is what makes paying before the group fills
safe: nothing moves until the group is repriced at the deadline, or dissolved.
"""

import threading
from contextlib import contextmanager
from decimal import Decimal

from indico.core import signals
from indico.core.db import db
from indico.modules.events.registration.models.registrations import RegistrationState

from indico_group_registration.plans import discount_for, discountable_amount, quantize
from indico_group_registration.util import get_discount_data, get_group_settings, set_discount_data


#: Set while we are writing prices, so that the ``registration_state_updated``
#: handler does not chase its own tail when `sync_state` flips a member from
#: complete to unpaid.
_reentrancy = threading.local()


@contextmanager
def pricing_in_progress():
    """Mark a stretch of our own price writes."""
    _reentrancy.active = getattr(_reentrancy, 'active', 0) + 1
    try:
        yield
    finally:
        _reentrancy.active -= 1


def is_pricing_in_progress():
    return getattr(_reentrancy, 'active', 0) > 0


def _other_items_total(registration, discount_data):
    """Everything billable on the registration except our own discount line."""
    total = Decimal(0)
    for data in registration.data:
        if discount_data is not None and data is discount_data:
            continue
        total += Decimal(str(data.price or 0))
    return total


def compute_discount(registration, plan, applies_to):
    """What this registration's discount is worth under `plan`."""
    discount_data = get_discount_data(registration)
    base_price = Decimal(str(registration.base_price or 0))
    other_items = _other_items_total(registration, discount_data)
    discountable = discountable_amount(base_price, other_items, applies_to)
    return discount_for(plan, discountable)


def apply_member_pricing(member, plan, applies_to):
    """Write one member's discount line.  Returns the amount written."""
    registration = member.registration
    if registration is None or registration.is_deleted:
        return Decimal(0)

    amount = compute_discount(registration, plan, applies_to)
    group = member.group

    if amount == 0:
        # An empty value keeps the row out of `billable_data`, so no zero-value
        # "Group discount" line clutters the invoice.
        value = {}
    else:
        value = {
            'group': group.code,
            'group_name': group.name,
            'plan': plan.id if plan else None,
            'rate': str(plan.value) if plan and plan.has_discount else None,
            'rate_type': plan.type if plan and plan.has_discount else None,
            'amount': str(quantize(amount)),
        }

    set_discount_data(registration, value)
    member.applied_amount = quantize(amount)
    return member.applied_amount


def apply_group_pricing(group):
    """Write every member's discount line from the group's current plan.

    Runs on a join, on a plan switch, at reconciliation, and on dissolution.
    Paid members are **not** skipped: repricing them upward is exactly how a
    balance comes into existence, and skipping them would hide it.
    """
    settings = get_group_settings(group.registration_form)
    applies_to = settings.applies_to if settings else 'base'
    plan = group.pricing_plan

    written = []
    with pricing_in_progress():
        for member in group.members:
            amount = apply_member_pricing(member, plan, applies_to)
            written.append((member, amount))
        db.session.flush()
        for member, _amount in written:
            _sync(member.registration)
    return written


def clear_registration_pricing(registration):
    """Drop the discount from a registration that has left its group."""
    with pricing_in_progress():
        set_discount_data(registration, {})
        db.session.flush()
        _sync(registration)


def _sync(registration):
    """Let core re-derive the registration's state after a price change.

    For an unpaid member whose price rose this is a no-op; for one whose price
    fell to zero it completes them.  A paid registration is left alone by
    `sync_state` itself, because its transaction is still successful -- which
    is what `sync_balance_state` then has to correct.
    """
    if registration is None or registration.is_deleted:
        return
    registration.sync_state()
    sync_balance_state(registration)


def sync_balance_state(registration):
    """Show a member who owes a top-up as awaiting payment.

    Indico decides "paid" from the transaction alone, so a member whose group
    was repriced upward after they paid still reads as settled everywhere it
    matters -- the registrant list, their own page, and the data the check-in
    app is given.  A balance nobody is shown is a balance nobody collects, so
    the registration goes back to `unpaid` for exactly as long as one is due,
    and returns to `complete` once it is not.

    Only ever moves between those two states: a pending, rejected or withdrawn
    registration is somebody else's decision and is left alone.
    """
    membership = registration.group_membership
    if membership is None:
        return
    previous_state = registration.state
    owes_balance = membership.balance_due > 0
    if owes_balance and previous_state == RegistrationState.complete:
        registration.state = RegistrationState.unpaid
    elif not owes_balance and previous_state == RegistrationState.unpaid and registration.is_paid:
        registration.state = RegistrationState.complete
    else:
        return
    signals.event.registration_state_updated.send(registration, previous_state=previous_state)
