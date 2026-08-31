"""Lookups and provisioning helpers shared by the rest of the plugin."""

from indico.core.db import db
from indico.modules.events.registration.models.form_fields import RegistrationFormField
from indico.modules.events.registration.models.items import RegistrationFormSection
from indico.util.i18n import _

from indico_group_registration.constants import (DISCOUNT_FIELD, DISCOUNT_FIELD_TITLE, DISCOUNT_SECTION_TITLE,
                                                 MODE_NONE, PLAN_FIELD, PLAN_FIELD_TITLE)
from indico_group_registration.models.groups import CODE_ALPHABET, CODE_LENGTH, GroupState, RegistrationGroup
from indico_group_registration.plans import parse_plans


def get_group_settings(regform):
    """The plugin's configuration for a registration form, or ``None``."""
    return regform.group_settings


def is_enabled(regform):
    settings = get_group_settings(regform)
    return settings is not None and settings.enabled


def get_plans(regform):
    """The plan table, or an empty tuple if it is missing or unusable.

    Participant-facing code must never blow up over a plan table an organizer
    has mangled; the management form validates it properly on save.
    """
    settings = get_group_settings(regform)
    if settings is None:
        return ()
    try:
        return parse_plans(settings.plans)
    except Exception:
        return ()


def normalize_code(code):
    """Accept a code however it was pasted: lowercase, spaced, hyphenated."""
    if not code:
        return ''
    return ''.join(c for c in code.upper() if c in CODE_ALPHABET)[:CODE_LENGTH]


def format_code(code):
    """Group codes are read aloud and retyped, so show them in two halves."""
    if not code or len(code) != CODE_LENGTH:
        return code or ''
    half = CODE_LENGTH // 2
    return f'{code[:half]}-{code[half:]}'


def find_group_by_code(regform, code):
    normalized = normalize_code(code)
    if not normalized:
        return None
    return (RegistrationGroup.query
            .filter_by(registration_form_id=regform.id, code=normalized)
            .first())


def find_group_by_uuid(regform, join_uuid):
    return (RegistrationGroup.query
            .filter_by(registration_form_id=regform.id, join_uuid=str(join_uuid))
            .first())


def resolve_join_target(regform, code):
    """Check whether a code can be joined right now.

    Returns ``(group, error)``.  This is the friendly, pre-submit check; the
    authoritative one runs under a row lock in
    `indico_group_registration.operations.join_group`, because between this
    call and that one somebody else can take the last seat.
    """
    group = find_group_by_code(regform, code)
    if group is None:
        return None, _('No group with that code.')
    if group.state == GroupState.dissolved:
        return None, _('That group no longer exists.')
    if group.state != GroupState.forming:
        return None, _('That group is already closed.')
    if group.is_full:
        return None, _('That group is full.')
    return group, None


def get_membership(registration):
    return registration.group_membership


def get_switchable_plans(group):
    """The plans a forming group's leader could still move it onto.

    Empty whenever switching is impossible, so the panel can simply hide the
    control rather than reproduce the rules `operations.switch_plan` enforces:
    the group must still be forming, nobody may have paid, and a plan has to
    seat everybody who has already joined.
    """
    from indico_group_registration.models.groups import GroupState
    from indico_group_registration.plans import group_plans

    if group.state != GroupState.forming:
        return ()
    if any(member.registration is not None and member.registration.is_paid for member in group.members):
        return ()
    count = group.member_count
    options = tuple(plan for plan in group_plans(group.plans) if plan.size >= count)
    # A single option that is the plan they are already on is not a choice.
    return options if len(options) > 1 else ()


# -- field provisioning ------------------------------------------------------
#
# The discount field has to exist on the form as a real item, because a
# RegistrationData row must point at a RegistrationFormFieldData.  We create it
# on demand and put it back if an organizer removes it.

def find_field(regform, input_type):
    """Find one of our fields on a form, including disabled ones."""
    return next((item for item in regform.form_items
                 if item.input_type == input_type and not item.is_deleted), None)


def _create_section(regform, title, *, manager_only):
    section = RegistrationFormSection(registration_form=regform, title=title, is_manager_only=manager_only)
    db.session.add(section)
    db.session.flush()
    return section


def _create_field(regform, section, input_type, title):
    # `parent=` rather than `section.children.append(field)`.  Appending forces
    # the section's `children` collection to load, and that query autoflushes
    # the half-built field -- which at that point still has no `parent_id` and
    # so trips the `ck_form_items_top_level_sections` check constraint.  Setting
    # the many-to-one side never loads the collection, which is also how core's
    # own `RHRegistrationFormAddField` does it.
    field = RegistrationFormField(parent=section, registration_form=regform, input_type=input_type,
                                  title=title, is_required=False)
    field.data, field.versioned_data = field.field_impl.process_field_data({})
    db.session.add(field)
    db.session.flush()
    return field


def provision_discount_field(regform):
    """Make sure the invoice line item exists.

    It lives in a manager-only section, which keeps it out of the participant's
    form and out of their answer summary while still letting it appear on the
    invoice -- the invoice table iterates billable data flat, without looking
    at sections.
    """
    field = find_field(regform, DISCOUNT_FIELD)
    if field is not None:
        if not field.is_enabled:
            field.is_enabled = True
        return field
    section = _create_section(regform, DISCOUNT_SECTION_TITLE, manager_only=True)
    return _create_field(regform, section, DISCOUNT_FIELD, DISCOUNT_FIELD_TITLE)


def provision_plan_field(regform):
    """Make sure the participant-facing plan picker exists."""
    field = find_field(regform, PLAN_FIELD)
    if field is not None:
        if not field.is_enabled:
            field.is_enabled = True
        return field
    section = _create_section(regform, PLAN_FIELD_TITLE, manager_only=False)
    return _create_field(regform, section, PLAN_FIELD, PLAN_FIELD_TITLE)


def provision_fields(regform):
    """Both of them, in the order they should appear."""
    return provision_plan_field(regform), provision_discount_field(regform)


def get_discount_data(registration):
    """The `RegistrationData` row holding this registration's discount."""
    field = find_field(registration.registration_form, DISCOUNT_FIELD)
    if field is None:
        return None
    return registration.data_by_field.get(field.id)


def set_discount_data(registration, value):
    """Write the discount value, creating the data row if it is missing.

    Core never creates this row for us: the field is locked, and locked fields
    are skipped in both `create_registration` and `modify_registration`.
    """
    from indico.modules.events.registration.models.registrations import RegistrationData

    field = provision_discount_field(registration.registration_form)
    data = registration.data_by_field.get(field.id)
    if data is None:
        # Passing `registration=` already puts the row into `registration.data`
        # through the backref.  Appending it again leaves the same object in the
        # collection twice, and every in-session price calculation -- the
        # confirmation e-mail's included -- then counts the discount twice.
        data = RegistrationData(registration=registration, field_data=field.current_data)
    else:
        # Point at the current version so the row does not pin an old one.
        data.field_data = field.current_data
    data.data = value
    return data


def clear_plan_choice(registration):
    """Forget what a registration asked for once it is out of its group.

    Leaving a group has to clear the answer, not just the membership: the
    answer is what `handlers.handle_registration_updated` reads, so a stale
    "join ABCD-2345" would quietly put the person back into the group they left
    the next time they edited anything on their registration.
    """
    field = find_field(registration.registration_form, PLAN_FIELD)
    if field is None:
        return
    data = registration.data_by_field.get(field.id)
    if data is None:
        return
    data.data = {'mode': MODE_NONE}


def get_plan_choice(registration):
    """What the participant chose on the form, as a plain dict."""
    field = find_field(registration.registration_form, PLAN_FIELD)
    if field is None:
        return {}
    data = registration.data_by_field.get(field.id)
    return (data.data if data else None) or {}
