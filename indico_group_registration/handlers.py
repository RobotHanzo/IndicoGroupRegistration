"""What the plugin does when Indico tells it something happened."""

from indico.util.i18n import _

from indico_group_registration.constants import DISCOUNT_FIELD, MODE_CREATE, MODE_JOIN, MODE_NONE, PLAN_FIELD
from indico_group_registration.operations import GroupError, create_group, join_group, leave_group, recount_group
from indico_group_registration.plans import get_plan
from indico_group_registration.pricing import is_pricing_in_progress
from indico_group_registration.util import (find_group_by_code, get_group_settings, get_plan_choice, get_plans,
                                            is_enabled)


def handle_registration_created(registration):
    """Put a brand-new registration into the group it asked for.

    Raising here rolls the registration back, which is the right outcome: the
    alternative is quietly registering somebody at full price when they thought
    they were joining a group.
    """
    regform = registration.registration_form
    if not is_enabled(regform):
        return

    choice = get_plan_choice(registration)
    mode = choice.get('mode', MODE_NONE)
    if mode == MODE_NONE:
        return

    settings = get_group_settings(regform)
    disclaimer_version = settings.disclaimer_version if choice.get('accepted') else None

    if mode == MODE_CREATE:
        plan = get_plan(get_plans(regform), choice.get('plan'))
        if plan is None:
            raise GroupError(_('That group plan no longer exists.'))
        create_group(registration, plan, choice.get('name'), disclaimer_version=disclaimer_version)
    elif mode == MODE_JOIN:
        group = find_group_by_code(regform, choice.get('code'))
        if group is None:
            raise GroupError(_('No group with that code.'))
        join_group(registration, group, disclaimer_version=disclaimer_version)


def handle_registration_deleted(registration):
    """Free the seat a deleted registration was holding."""
    if registration.group_membership is None:
        return
    leave_group(registration)


def handle_registration_state_updated(registration):
    """Recount after a state change that may have crossed the counting line.

    Withdrawal and rejection free a seat; approval can fill the last one and
    auto-confirm the group.
    """
    if is_pricing_in_progress():
        # Our own `sync_state` calls land here; they never change who counts.
        return
    membership = registration.group_membership
    if membership is None:
        return
    recount_group(membership.group)


def get_locked_field_reason(form_item, registration):
    """Keep both of our fields out of core's read/write paths.

    The discount field is ours entirely -- core skipping it is what lets us own
    the `RegistrationData` row.  The plan field locks only once a membership
    exists, so that editing a registration cannot silently re-do group
    membership behind the group's back.
    """
    if form_item.input_type == DISCOUNT_FIELD:
        return _('This is set automatically by the group registration plugin.')
    if (form_item.input_type == PLAN_FIELD
            and registration is not None and registration.group_membership is not None):
        return _('Manage your group from the group panel on your registration page.')
    return None
