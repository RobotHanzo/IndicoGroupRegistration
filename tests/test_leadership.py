"""Leadership is authority over other people's money, so it has to stay live.

The two leader-only endpoints are not small: `switch_plan` rewrites what every
remaining member owes, and *New join link* invalidates every copy of the link
the group has already handed out.  Nothing, though, takes the group off a
leader whose own registration dies -- core's `RegistrationForm.get_registration`
hands back withdrawn and rejected registrations just the same, the `GroupMember`
row outlives a state change, and leadership passes on only when the leader
*leaves*.  So the check has to ask about the caller as well as about the group,
and an id comparison on its own does not.

Leaving is deliberately outside that rule: it costs nobody but the person doing
it, and a member the organizer has rejected still has to be able to get their
registration out of the group.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from indico.modules.events.registration.models.registrations import RegistrationState
from werkzeug.exceptions import Forbidden

from indico_group_registration.controllers import display


GROUP_PANEL = Path(__file__).parents[1] / 'indico_group_registration' / 'templates' / '_group_panel.html'

LEADER_ID = 11
MEMBER_ID = 12

#: The states core's `Registration.is_active` says no to -- the ones an
#: organizer's rejection or the participant's own withdrawal leave behind.
DEAD_STATES = (RegistrationState.rejected, RegistrationState.withdrawn)

#: Everything else a member can be while still in the event.  A group forms
#: long before anybody has paid, so these are the ordinary case, not an edge.
LIVE_STATES = (RegistrationState.complete, RegistrationState.pending, RegistrationState.unpaid)


def make_registration(registration_id, state):
    """A stand-in registration that answers `is_active` the way core's does."""
    return SimpleNamespace(id=registration_id, state=state, is_active=state not in DEAD_STATES)


@pytest.fixture
def leader_rh(monkeypatch):
    """A leader-only handler with only this plugin's half of the check left.

    The other half is core's: `RHRegistrationFormRegistrationBase._check_access`
    wants a request, a session and an event, none of which this is about.  The
    handler is built without running `__init__` and handed the two attributes
    `_process_args` would have put on it.
    """
    monkeypatch.setattr(display.RHGroupBase, '_check_access', lambda self: None)

    def make(registration, *, leader_registration_id=LEADER_ID):
        rh = display.RHGroupLeaderBase.__new__(display.RHGroupLeaderBase)
        rh.registration = registration
        rh.group = SimpleNamespace(leader_registration_id=leader_registration_id)
        return rh

    return make


class TestOnlyALiveLeaderMayLead:
    @pytest.mark.parametrize('state', LIVE_STATES)
    def test_the_leader_may_act(self, leader_rh, state):
        """Nothing changes for a leader who is still in the event.

        Including one who has not paid, or whose registration the organizer has
        not approved yet: only the two states core calls inactive are refused.
        """
        leader_rh(make_registration(LEADER_ID, state))._check_access()

    @pytest.mark.parametrize('state', DEAD_STATES)
    def test_a_withdrawn_or_rejected_leader_may_not(self, leader_rh, state):
        """The whole point: still the recorded leader, no longer a participant.

        Left through, they can move the group onto a plan with a smaller
        discount -- everyone unpaid is repriced upward on the spot -- or onto
        one the group can never fill, which does the same at the deadline.
        """
        rh = leader_rh(make_registration(LEADER_ID, state))
        with pytest.raises(Forbidden):
            rh._check_access()

    def test_an_ordinary_member_may_not(self, leader_rh):
        rh = leader_rh(make_registration(MEMBER_ID, RegistrationState.complete))
        with pytest.raises(Forbidden):
            rh._check_access()


def test_leaving_is_not_a_leader_action():
    """The one thing the check above must not reach.

    `leave_group` takes away nobody's discount but the caller's own, and a
    member whose registration has been rejected or withdrawn is exactly the
    person who may need it -- so `RHLeaveGroup` stays on `RHGroupBase`.
    """
    assert issubclass(display.RHLeaveGroup, display.RHGroupBase)
    assert not issubclass(display.RHLeaveGroup, display.RHGroupLeaderBase)


def test_the_panel_stops_offering_what_the_server_refuses():
    """A button that can only answer 403 is worse than no button.

    The panel decides in one place who gets *Switch* and *New join link*, and
    that decision has to ask the same question `RHGroupLeaderBase._check_access`
    does.  Read as text: the template needs Jinja and a group to render.
    """
    gate = re.search(r'\{%\s*set is_leader =([^%]*)%\}', GROUP_PANEL.read_text(encoding='utf-8'))
    assert gate, 'the group panel no longer decides who the leader is in one place'
    assert 'is_active' in gate.group(1), (
        'the group panel offers the leader-only buttons to a leader whose registration is withdrawn or '
        'rejected, which RHGroupLeaderBase._check_access refuses'
    )
