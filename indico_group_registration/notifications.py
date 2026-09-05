"""E-mails the plugin sends.

None of these can be triggered on demand by a participant.  Group leaders share
their code and link themselves; the plugin writes when the group's own state
changes, and once more when an *organizer* asks it to remind the groups that
have not filled yet.
"""

from decimal import Decimal

from indico.core.notifications import make_email, send_email
from indico.core.plugins import get_plugin_template_module

from indico_group_registration.plans import APPLIES_TO_BASE
from indico_group_registration.pricing import projected_price


def _live_members(group):
    for member in group.members:
        registration = member.registration
        if registration is None or registration.is_deleted or not registration.is_active:
            continue
        yield member, registration


def _send(group, member, registration, template_name, **context):
    template = get_plugin_template_module(
        f'emails/{template_name}',
        group=group,
        member=member,
        registration=registration,
        regform=group.registration_form,
        event=group.event,
        **context,
    )
    send_email(make_email(registration.email, template=template), group.event, 'Registration')


def notify_group_confirmed(group):
    """The group filled: the rate everyone is on is now final."""
    for member, registration in _live_members(group):
        _send(group, member, registration, 'group_confirmed.txt')


def notify_group_short(group, outcomes):
    """The group did not fill and has been repriced.

    `outcomes` maps a member id to the ``(old_price, new_price)`` pair, so the
    e-mail can say what changed rather than only what is now true.
    """
    for member, registration in _live_members(group):
        old_price, new_price = outcomes.get(member.id, (None, registration.price))
        _send(group, member, registration, 'group_short.txt',
              old_price=old_price,
              new_price=new_price,
              balance_due=member.balance_due,
              paid_amount=member.paid_amount)


def notify_group_dissolved(group):
    """A manager took the group apart."""
    for member, registration in _live_members(group):
        _send(group, member, registration, 'group_dissolved.txt')


def notify_group_reminder(group, deadline):
    """An organizer's nudge to a group that has not filled yet.

    Every live member gets one, not only the leader: it is each member's own
    price that moves if the group falls short, so each e-mail carries that
    member's figures -- what they pay now, what they would pay if the group
    were repriced at its current size, and what that would leave them owing.
    The fallback plan is the one `reconcile.reconcile_group` would pick at the
    deadline, so the amounts quoted are the amounts that would be charged.

    Returns how many e-mails were sent.
    """
    settings = group.settings
    applies_to = settings.applies_to if settings else APPLIES_TO_BASE
    fallback_plan = group.resolve_shortfall_plan()
    sent = 0
    for member, registration in _live_members(group):
        current_price = registration.price
        projected = projected_price(registration, fallback_plan, applies_to)
        paid_amount = member.paid_amount
        _send(group, member, registration, 'group_reminder.txt',
              deadline=deadline,
              seats_left=group.seats_left,
              fallback_plan=fallback_plan,
              current_price=current_price,
              projected_price=projected,
              difference=max(projected - current_price, Decimal(0)),
              paid_amount=paid_amount,
              projected_balance=max(projected - paid_amount, Decimal(0)))
        sent += 1
    return sent
