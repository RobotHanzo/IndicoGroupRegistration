"""What the organizer's reminder to the groups still forming says to begin with.

Pure, the same split as `plans.py`: the wording is decided from nothing but
constants, so it is testable without a database or a request.  Finding the
people it goes to is `controllers.management`, and working out their figures is
`placeholders`.

The wording is a Python string rather than a Jinja e-mail template on purpose.
It is a starting point, not a template: the organizer reads and edits it in the
dialog before anything is sent, and core's own placeholder machinery fills in
`{first_name}`, `{link}` and our `{group_*}` figures per recipient at send time
-- so there is nothing left for a template module to do except make the default
harder to test.
"""

from indico.util.i18n import _


def default_subject():
    """The subject the reminder dialog opens with."""
    return str(_('Your group "{group_name}" is not confirmed yet'))


def default_body():
    """The reminder an organizer starts from.

    HTML, because the dialog's editor is a rich text one, and every name in
    braces is a placeholder core replaces per recipient -- so this one string
    becomes a different mail for each person it goes to.

    Every piece is forced to `str` as it goes in.  Indico's `_` hands back a
    lazy string that only picks a translation when it is rendered, which is
    exactly right for a form label defined at import time and exactly wrong
    here: this is called inside the event's locale, and the answer has to be a
    real string that a text field can hold and an organizer can edit.

    One body goes to everybody, so nothing in it may depend on a figure being
    non-zero.  That is why it says what a member *would pay* and *would owe*
    rather than "a difference of" or "you have already paid": both read
    correctly whether or not the amount moves, and `{group_difference}` is
    there for an organizer whose groups all stand to pay more.
    """
    parts = (
        '<p>', _('Dear {first_name},'), '</p>',
        '<p>', _('Your group "{group_name}" for <strong>{event_title}</strong> has {group_members} of the '
                 '{group_target} members its "{group_plan}" rate requires, so it is not confirmed yet.'), '</p>',
        '<p>', _('If it has not filled by <strong>{group_deadline}</strong>, it will be repriced to the rate it '
                 'does qualify for at that point. That applies to everyone in the group, whether or not they have '
                 'already paid.'), '</p>',
        '<p>', _('At its current size the group would be charged the {group_fallback_plan} rate: the amount '
                 'payable for your registration would be <strong>{group_new_price}</strong> instead of '
                 '{group_price}, leaving <strong>{group_balance}</strong> to pay. Registrations with an unpaid '
                 'balance may be cancelled, and entry to the event may be refused.'), '</p>',
        '<p>', _('There is still time -- seats left to fill: {group_seats_left}. Share the group code '
                 '<strong>{group_code}</strong> or this link with anyone who still means to register, and the '
                 'group confirms itself the moment the last seat is taken:'), '<br>{group_link}</p>',
        '<p>', _('You can see your group and its members on your registration page:'), '<br>{link}</p>',
    )
    return ''.join(str(part) for part in parts)
