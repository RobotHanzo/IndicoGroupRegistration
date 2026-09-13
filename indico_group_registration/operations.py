"""Everything that changes a group, always under a row lock.

The lock is not decoration.  Two people submitting into the last two seats of a
ten-seat group will otherwise both read a count of nine, and the group either
overfills or never trips its own auto-confirm.
"""

from indico.core.db import db
from indico.core.errors import UserValueError
from indico.modules.events.registration.models.registrations import Registration, RegistrationState
from indico.util.date_time import now_utc
from indico.util.i18n import _

from indico_group_registration.constants import MAX_GROUP_NAME_LENGTH
from indico_group_registration.models.groups import GroupState, RegistrationGroup, generate_code
from indico_group_registration.models.members import GroupMember
from indico_group_registration.plans import best_plan_for_size, get_plan
from indico_group_registration.pricing import apply_group_pricing, clear_registration_pricing
from indico_group_registration.util import clear_plan_choice, get_group_settings


#: Registrations in these states occupy a seat.
LIVE_STATES = (RegistrationState.complete, RegistrationState.unpaid)

CODE_ATTEMPTS = 10


class GroupError(UserValueError):
    """Something the participant did that we can explain to them."""


def lock_group(group):
    """Take a row lock on the group and return the freshly-read row.

    Everything that reads a seat count in order to act on it must go through
    here first.
    """
    locked = (db.session.query(RegistrationGroup)
              .filter(RegistrationGroup.id == group.id)
              .with_for_update()
              .one())
    # `expire`, not `refresh`: SQLAlchemy refuses to refresh a name that is a
    # relationship rather than a column ("No column-based properties specified
    # for refresh operation").  Expiring it drops the stale collection so the
    # next access reloads it inside the lock, which is all we are after.
    db.session.expire(locked, ['members'])
    return locked


def count_qualifying(group):
    """Seats currently taken, read from the database rather than the session."""
    settings = get_group_settings(group.registration_form)
    states = set(LIVE_STATES)
    if settings is None or settings.count_pending:
        states.add(RegistrationState.pending)
    return (db.session.query(db.func.count(GroupMember.id))
            .join(GroupMember.registration)
            .filter(GroupMember.group_id == group.id,
                    ~Registration.is_deleted,
                    Registration.state.in_(states))
            .scalar()) or 0


def _generate_unique_code(regform):
    for _attempt in range(CODE_ATTEMPTS):
        code = generate_code()
        exists = (db.session.query(RegistrationGroup.id)
                  .filter_by(registration_form_id=regform.id, code=code)
                  .first())
        if exists is None:
            return code
    # 31^8 codes and ten tries; if we get here something is very wrong.
    raise GroupError(_('Could not allocate a group code. Please try again.'))


def create_group(registration, plan, name, *, disclaimer_version=None):
    """Open a new group led by `registration`."""
    regform = registration.registration_form
    settings = get_group_settings(regform)
    if settings is None or not settings.enabled:
        raise GroupError(_('Group registration is not available for this form.'))
    if registration.group_membership is not None:
        raise GroupError(_('You are already in a group.'))
    if not plan.is_group:
        raise GroupError(_('That plan is not a group plan.'))

    name = (name or '').strip()[:MAX_GROUP_NAME_LENGTH]
    if not name:
        raise GroupError(_('Please give your group a name.'))

    if registration.user is not None and settings.max_groups_per_user:
        led = (db.session.query(db.func.count(RegistrationGroup.id))
               .join(RegistrationGroup.leader_registration)
               .filter(RegistrationGroup.registration_form_id == regform.id,
                       RegistrationGroup.state != GroupState.dissolved,
                       Registration.user_id == registration.user.id)
               .scalar()) or 0
        if led >= settings.max_groups_per_user:
            raise GroupError(_('You have already created the maximum number of groups.'))

    group = RegistrationGroup(
        registration_form=regform,
        code=_generate_unique_code(regform),
        name=name,
        plan_id=plan.id,
        target_size=plan.size,
        effective_plan_id=plan.id,
        state=GroupState.forming,
        leader_registration=registration,
    )
    db.session.add(group)
    db.session.flush()

    _add_member(group, registration, disclaimer_version=disclaimer_version)
    db.session.flush()
    apply_group_pricing(group)
    recount_group(group)
    return group


def join_group(registration, group, *, disclaimer_version=None):
    """Put `registration` into an existing group.

    The checks here are the authoritative ones -- the form's validator only
    produces a friendlier error earlier on.
    """
    if registration.group_membership is not None:
        raise GroupError(_('You are already in a group.'))

    group = lock_group(group)
    if group.state == GroupState.dissolved:
        raise GroupError(_('That group no longer exists.'))
    if group.state != GroupState.forming:
        raise GroupError(_('That group is already closed.'))
    if count_qualifying(group) >= group.target_size:
        raise GroupError(_('That group is full.'))

    member = _add_member(group, registration, disclaimer_version=disclaimer_version)
    db.session.flush()
    apply_group_pricing(group)
    recount_group(group)
    return member


def _add_member(group, registration, *, disclaimer_version=None):
    member = GroupMember(
        group=group,
        registration=registration,
        disclaimer_version=disclaimer_version,
        disclaimer_accepted_dt=(now_utc() if disclaimer_version is not None else None),
    )
    db.session.add(member)
    return member


def leave_group(registration, *, reason=None):
    """Remove a registration from its group and put it back on the full rate."""
    membership = registration.group_membership
    if membership is None:
        return None

    group = lock_group(membership.group)
    db.session.delete(membership)
    db.session.flush()
    clear_registration_pricing(registration)
    clear_plan_choice(registration)

    if group.leader_registration_id == registration.id:
        _transfer_leadership(group)

    remaining = [m for m in group.members if m.registration_id != registration.id]
    if not remaining:
        db.session.delete(group)
        db.session.flush()
        return group

    recount_group(group)
    return group


def _transfer_leadership(group):
    """Hand the group to whoever joined earliest among the people left."""
    successor = next((m for m in sorted(group.members, key=lambda m: m.joined_dt)
                      if m.registration_id != group.leader_registration_id), None)
    group.leader_registration_id = successor.registration_id if successor else None


def switch_plan(group, plan):
    """Change a forming group's plan.

    Only while nobody has paid: otherwise one person's change would silently
    rebill everybody else, including people who have already handed over money.
    """
    group = lock_group(group)
    if group.state != GroupState.forming:
        raise GroupError(_('This group can no longer change its plan.'))
    if any(member.registration and member.registration.is_paid for member in group.members):
        raise GroupError(_('The plan cannot be changed once a member has paid.'))
    if not plan.is_group:
        raise GroupError(_('That plan is not a group plan.'))
    if plan.size < count_qualifying(group):
        raise GroupError(_('That plan has fewer seats than the group already has members.'))

    group.plan_id = plan.id
    group.target_size = plan.size
    group.effective_plan_id = plan.id
    db.session.flush()
    apply_group_pricing(group)
    recount_group(group)
    return group


def dissolve_group(group):
    """Take a group apart and put every member back on the standard rate."""
    group = lock_group(group)
    group.state = GroupState.dissolved
    group.effective_plan_id = None
    db.session.flush()
    apply_group_pricing(group)
    return group


def recount_group(group):
    """React to the seat count having changed.

    This is the auto-lock: reaching the plan's seat count confirms the group,
    with no button anywhere.  A dissolved group is past all of this -- a
    manager decided its rate.  A short one is not: the deadline priced it on
    the seats it had at the time, and a seat can still come back.
    """
    if group.state not in (GroupState.forming, GroupState.confirmed, GroupState.short):
        return group

    count = count_qualifying(group)

    if group.state == GroupState.short:
        return _restore_short_group(group, count)

    if group.state == GroupState.forming and count >= group.target_size:
        group.state = GroupState.confirmed
        group.confirmed_dt = now_utc()
        db.session.flush()
        from indico_group_registration.notifications import notify_group_confirmed
        notify_group_confirmed(group)
        return group

    # Back to forming, not straight to a new price: a forming group is charged
    # its chosen plan just as a confirmed one is, so nobody is rebilled on the
    # spot.  What the group loses is its exemption from reconciliation -- which
    # is exactly what `templates/emails/group_confirmed.txt` has to have said.
    if group.state == GroupState.confirmed and count < group.target_size and group.reprices_on_member_loss:
        group.state = GroupState.forming
        group.confirmed_dt = None
        db.session.flush()
        apply_group_pricing(group)

    return group


def _restore_short_group(group, count):
    """Give back the rate a returning member re-earns.

    Reconciliation is a verdict on how big the group was at the deadline, and
    that is not always the last word on how big it is: an organizer can
    un-withdraw somebody, reverse a rejection, or approve a member on a form
    that does not count pending ones.  Without this, a group that is whole
    again goes on paying for the size it briefly was -- and the one member who
    withdrew by mistake cannot be put back without the rest of their group
    keeping the bill for it.

    Upward only, and deliberately so.  A short group that loses *another*
    member keeps the rate it was reconciled onto: charging people more is what
    the deadline is for, and doing it as a side effect of an organizer editing
    one registration would open balances with no notice and nothing to point
    at.  Taking a rate away is `dissolve_group`, which says so and writes to
    everybody.
    """
    refilled = count >= group.target_size
    if refilled:
        plan_id = group.plan_id
    else:
        best = best_plan_for_size(group.plans, count)
        current = get_plan(group.plans, group.effective_plan_id)
        # Plan sizes are unique, so the seat count is the whole ordering:
        # a bigger plan is one the group did not qualify for before.
        if best is None or best.size <= (current.size if current is not None else 0):
            return group
        plan_id = best.id

    old_prices = {member.id: member.registration.price
                  for member in group.members if member.registration is not None}

    group.effective_plan_id = plan_id
    if refilled:
        # Exactly where the group would have been had the seat never emptied:
        # confirmed on its chosen plan, and no longer carrying a verdict.  If
        # it loses a member again `recount_group` drops it back to forming and
        # the deadline reprices it like any other group that did not fill.
        group.state = GroupState.confirmed
        group.confirmed_dt = now_utc()
        group.reconciled_dt = None
    else:
        group.reconciled_dt = now_utc()
    db.session.flush()

    apply_group_pricing(group)

    outcomes = {member.id: (old_prices.get(member.id), member.registration.price)
                for member in group.members if member.registration is not None}
    from indico_group_registration.notifications import notify_group_restored
    notify_group_restored(group, outcomes)
    return group
