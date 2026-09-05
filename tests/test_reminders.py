"""Every name in braces in the default reminder has to be a real placeholder.

The reminder is one body sent to every member of every forming group, and each
figure in it reaches the mail only because core's placeholder machinery
replaces it per recipient.  A name that nothing registers is not an error
anywhere -- `replace_placeholders` simply leaves it alone -- so a typo would go
out to participants as literal braces, quoting no price at all.  These tests
are the only thing standing between that and a mailbox.
"""

import re

import pytest

from indico_group_registration.placeholders import GROUP_PLACEHOLDERS
from indico_group_registration.reminders import default_body, default_subject


#: The `registration-email` placeholders core itself registers
#: (`indico/modules/events/registration/__init__.py`).  Ours are checked against
#: the classes the plugin actually yields, so only core's have to be listed.
CORE_PLACEHOLDERS = {
    'first_name', 'last_name', 'picture', 'event_title', 'event_link', 'id', 'link', 'rejection_reason', 'field',
}


def _placeholders_in(text):
    return set(re.findall(r'\{(\w+)}', text))


@pytest.mark.parametrize('get_default', (default_subject, default_body))
def test_every_placeholder_used_is_registered(get_default):
    known = CORE_PLACEHOLDERS | {placeholder.name for placeholder in GROUP_PLACEHOLDERS}
    assert _placeholders_in(get_default()) <= known


def test_the_group_placeholder_names_are_prefixed():
    """Two plugins claiming one name makes `named_objects_from_signal` raise.

    It raises for *every* registration e-mail in the instance, not only the
    ones using the placeholder, so the prefix is what keeps this plugin from
    breaking somebody else's mail.
    """
    for placeholder in GROUP_PLACEHOLDERS:
        assert placeholder.name.startswith('group_')


def test_the_group_placeholder_names_are_unique():
    names = [placeholder.name for placeholder in GROUP_PLACEHOLDERS]
    assert len(set(names)) == len(names)


def test_every_group_placeholder_describes_itself():
    # The dialog lists them by name and description; one without a description
    # is a figure an organizer has no way to identify.
    for placeholder in GROUP_PLACEHOLDERS:
        assert placeholder.description


def test_the_default_wording_is_a_real_string():
    """Not one of Indico's lazy strings.

    `_` hands back a proxy that only picks a translation when it is rendered,
    which is right for a label defined at import time and wrong here: this goes
    into a text field the organizer edits, inside the event's locale.
    """
    assert type(default_subject()) is str
    assert type(default_body()) is str


def test_the_body_quotes_the_figures_that_make_it_worth_sending():
    # The deadline and what the member would pay are the reason the mail
    # exists; a default that dropped either would still send cleanly.
    used = _placeholders_in(default_body())
    assert {'group_deadline', 'group_new_price', 'group_code', 'group_link'} <= used
