"""E-mails the plugin sends.

None of these can be triggered on demand by a participant.  Group leaders share
their code and link themselves; the plugin only writes when the group's own
state changes.
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
