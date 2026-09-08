"""The e-mails the plugin sends by itself.

None of these can be triggered on demand by a participant.  Group leaders share
their code and link themselves; the plugin writes only when the group's own
state changes.

The one mail an *organizer* sends by hand -- the reminder to the groups that
have not filled yet -- is not here.  Its wording is theirs to edit, so it goes
out through core's e-mail dialog instead; see `controllers.management` and
`reminders`.
"""

from indico.core.notifications import make_email, send_email
from indico.core.plugins import get_plugin_template_module


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
    """The group filled.

    Whether that settles the rate is the organizer's `revoke_on_member_loss`:
    with it on, a member who leaves or is rejected puts the group back to
    forming, and the mail has to say so rather than promise a price the
    plugin may take back.  The deadline goes in from here because that is the
    date the group would then be repriced on, worded exactly as the reminder
    words it.
    """
    # `reprices_on_member_loss` is false without a settings row, so this cannot
    # be reached without one.
    deadline = group.settings.format_reconciliation_dt() if group.reprices_on_member_loss else None
    for member, registration in _live_members(group):
        _send(group, member, registration, 'group_confirmed.txt', deadline=deadline)


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
