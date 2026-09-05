"""The deadline a reminder quotes must be the one the Celery task acts on.

`reconcile.effective_deadline_column` picks a group's deadline in SQL --
reconciliation date, then registration close, then event start -- and
`GroupSettings.get_reconciliation_dt` is the same chain in Python, for the
pages and e-mails that name the date.  These pin the two to each other by
exercising the Python side against stand-ins; the method never touches the
session, so it can be called unbound.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from indico_group_registration.models.settings import GroupSettings


RECONCILIATION = datetime(2026, 10, 1, 12, tzinfo=UTC)
CLOSE = datetime(2026, 10, 15, 12, tzinfo=UTC)
START = datetime(2026, 11, 1, 9, tzinfo=UTC)


def settings(reconciliation_dt=None, end_dt=None, start_dt=START):
    regform = SimpleNamespace(end_dt=end_dt, event=SimpleNamespace(start_dt=start_dt))
    return SimpleNamespace(reconciliation_dt=reconciliation_dt, registration_form=regform)


def test_the_forms_own_date_wins():
    assert GroupSettings.get_reconciliation_dt(settings(RECONCILIATION, CLOSE)) == RECONCILIATION


def test_falls_back_to_registration_closing():
    assert GroupSettings.get_reconciliation_dt(settings(None, CLOSE)) == CLOSE


def test_falls_back_to_the_event_start():
    # A form that never closes still gets a deadline, so a group can never sit
    # forming forever because an organizer left a date blank.
    assert GroupSettings.get_reconciliation_dt(settings(None, None)) == START
