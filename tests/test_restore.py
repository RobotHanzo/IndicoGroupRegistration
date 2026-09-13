"""Undoing a repricing when the seat that caused it comes back.

Reconciliation is a verdict on how big a group was at the deadline, and that is
not always the last word on how big it is: an organizer can un-withdraw
somebody, reverse a rejection, or approve a member on a form that does not
count pending ones.  Nobody can *join* a short group, so a state change is the
only thing that can move its seat count -- which is why `recount_group` is
where this has to be noticed.

The asymmetry is the part worth pinning.  A short group that gains a seat is
repriced down to what it now earns; one that loses another seat is left alone,
because charging people more is what the deadline is for, and doing it as a
side effect of an organizer's edit would open balances with nothing behind
them.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from indico_group_registration import notifications, operations
from indico_group_registration.models.groups import GroupState
from indico_group_registration.plans import Plan


NOW = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)
RECONCILED = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)

TRIO = Plan(id='p3', label='Trio', size=3, type='percent', value=Decimal(10))
QUINTET = Plan(id='p5', label='Quintet', size=5, type='percent', value=Decimal(20))
PLANS = (TRIO, QUINTET)


def make_group(state, *, effective_plan_id, target_size=5, plan_id='p5', size=5):
    """A group whose seat count the fixture decides, not its member list.

    `count_qualifying` reads the database rather than the collection, and so
    does everything under test; the members are here only so that the pricing
    call and the mail have something to report on.
    """
    return SimpleNamespace(
        id=1,
        state=state,
        target_size=target_size,
        plan_id=plan_id,
        effective_plan_id=effective_plan_id,
        plans=PLANS,
        confirmed_dt=None,
        reconciled_dt=RECONCILED,
        reprices_on_member_loss=True,
        members=[SimpleNamespace(id=n, registration=SimpleNamespace(price=Decimal('90.00')))
                 for n in range(size)],
    )


@pytest.fixture
def world(monkeypatch):
    """Everything `recount_group` reaches outside its own module."""
    record = SimpleNamespace(count=0, priced=[], restored=[], confirmed=[])
    monkeypatch.setattr(operations, 'count_qualifying', lambda group: record.count)
    monkeypatch.setattr(operations, 'db', SimpleNamespace(session=SimpleNamespace(flush=lambda: None)))
    monkeypatch.setattr(operations, 'apply_group_pricing', record.priced.append)
    monkeypatch.setattr(operations, 'now_utc', lambda: NOW)
    # Both notifications are imported inside the function that sends them, so
    # the patch has to land on the module the import reads from.
    monkeypatch.setattr(notifications, 'notify_group_restored',
                        lambda group, outcomes: record.restored.append((group, outcomes)))
    monkeypatch.setattr(notifications, 'notify_group_confirmed', record.confirmed.append)
    return record


class TestTheSeatComesBack:
    def test_a_refilled_group_is_confirmed_on_the_plan_it_chose(self, world):
        """The scenario this exists for: a full group, one withdraws, they return.

        Getting back to `forming` would not be enough -- the group is the size
        its own plan asks for, so it is confirmed, exactly where it would have
        been had the seat never emptied.
        """
        world.count = 5
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert group.state == GroupState.confirmed
        assert group.effective_plan_id == 'p5'
        assert group.confirmed_dt == NOW

    def test_it_stops_carrying_a_verdict(self, world):
        """`reconciled_dt` is what says the deadline ruled on this group."""
        world.count = 5
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert group.reconciled_dt is None

    def test_everyone_is_repriced(self, world):
        world.count = 5
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert world.priced == [group]

    def test_the_members_are_told_what_they_no_longer_owe(self, world):
        """Some of them will have been asked for a balance, and paid it.

        `notify_group_short` told them the amount had gone up; without this
        nothing in the plugin ever tells them it came back down.
        """
        world.count = 5
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert len(world.restored) == 1
        _group, outcomes = world.restored[0]
        assert set(outcomes) == {member.id for member in group.members}

    def test_the_confirmation_mail_is_not_sent_as_well(self, world):
        """One mail for one change; `group_restored.txt` says both halves of it."""
        world.count = 5
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert world.confirmed == []

    def test_a_partial_recovery_takes_the_plan_it_now_qualifies_for(self, world):
        """Three of five back is still short, but three is a plan of its own."""
        world.count = 3
        group = make_group(GroupState.short, effective_plan_id=None)
        operations.recount_group(group)
        assert group.state == GroupState.short
        assert group.effective_plan_id == 'p3'
        assert group.reconciled_dt == NOW
        assert world.priced == [group]

    def test_a_recovery_that_earns_nothing_changes_nothing(self, world):
        """Two members qualify for no group plan, so there is nothing to give back."""
        world.count = 2
        group = make_group(GroupState.short, effective_plan_id=None)
        operations.recount_group(group)
        assert group.effective_plan_id is None
        assert group.reconciled_dt == RECONCILED
        assert world.priced == []
        assert world.restored == []

    def test_the_same_plan_twice_is_not_a_repricing(self, world):
        """A seat change that crosses no plan boundary has to stay quiet.

        Otherwise every approval and every withdrawal in a short group writes
        to everybody in it about a rate that did not move.
        """
        world.count = 3
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert world.priced == []
        assert world.restored == []


class TestTheSeatDoesNotComeBack:
    def test_a_short_group_that_loses_another_member_keeps_its_rate(self, world):
        """Upward only.

        Repricing further up here would bill people in the middle of an
        organizer editing somebody else's registration, with no deadline behind
        it and nothing to point at.  `dissolve_group` is the action that takes
        a rate away, and it says so and writes to everyone.
        """
        world.count = 2
        group = make_group(GroupState.short, effective_plan_id='p3')
        operations.recount_group(group)
        assert group.effective_plan_id == 'p3'
        assert group.state == GroupState.short
        assert world.priced == []
        assert world.restored == []

    def test_a_dissolved_group_is_never_touched(self, world):
        world.count = 5
        group = make_group(GroupState.dissolved, effective_plan_id=None)
        operations.recount_group(group)
        assert group.state == GroupState.dissolved
        assert world.priced == []
        assert world.restored == []


class TestTheOtherStatesStillBehave:
    """The short branch is an addition, not a rewrite."""

    def test_a_forming_group_that_fills_confirms(self, world):
        world.count = 5
        group = make_group(GroupState.forming, effective_plan_id='p5')
        operations.recount_group(group)
        assert group.state == GroupState.confirmed
        assert world.confirmed == [group]
        assert world.restored == []

    def test_a_confirmed_group_that_loses_a_member_goes_back_to_forming(self, world):
        world.count = 4
        group = make_group(GroupState.confirmed, effective_plan_id='p5')
        operations.recount_group(group)
        assert group.state == GroupState.forming
        assert group.confirmed_dt is None
        assert world.priced == [group]
        assert world.restored == []
