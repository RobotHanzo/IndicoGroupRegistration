"""The two registration field types this plugin adds.

``ext__group_plan`` is what the participant fills in: create a group, join one,
or neither.  ``ext__group_discount`` is what the plugin fills in: the money.

They are separate on purpose.  The first is a one-off choice belonging to the
person; the second changes whenever the group does, and belongs to a
manager-only section so that it shows up on the invoice without cluttering the
participant's own answers.
"""

from decimal import Decimal, InvalidOperation

from markupsafe import escape
from marshmallow import ValidationError, fields, validate

from indico.core.marshmallow import mm
from indico.modules.events.registration.custom import RegistrationListColumn
from indico.modules.events.registration.fields.base import RegistrationFormFieldBase
from indico.util.i18n import _

from indico_group_registration.constants import (MAX_GROUP_NAME_LENGTH, MODE_CREATE, MODE_JOIN, MODE_NONE, MODES,
                                                 PLAN_FIELD, DISCOUNT_FIELD)
from indico_group_registration.plans import APPLIES_TO_BASE, get_plan


def _decimal(value):
    """Read a money amount out of JSON without ever raising."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


class GroupPlanValueSchema(mm.Schema):
    """Shape only.  The rules live in `GroupPlanField.get_validators`, which
    can see the registration form and therefore the plan table."""

    mode = fields.String(required=True, validate=validate.OneOf(sorted(MODES)))
    plan = fields.String(load_default=None, allow_none=True)
    name = fields.String(load_default='')
    code = fields.String(load_default='')
    accepted = fields.Bool(load_default=False)


class GroupPlanField(RegistrationFormFieldBase):
    """Lets a participant open a group or join one while registering."""

    name = PLAN_FIELD
    mm_field_class = fields.Nested
    mm_field_args = (GroupPlanValueSchema,)
    versioned_data_fields = frozenset()
    not_empty_if_required = False

    @property
    def default_value(self):
        return {'mode': MODE_NONE}

    @property
    def empty_value(self):
        return {'mode': MODE_NONE}

    @property
    def view_data(self):
        from indico_group_registration.util import get_group_settings, get_plans

        regform = self.form_item.registration_form
        settings = get_group_settings(regform)
        plans = get_plans(regform)
        return dict(
            super().view_data,
            # `view_data` is camelized on its way to the browser, so these
            # arrive as `basePrice`, `allowEarlyPayment` and so on.
            event_id=regform.event_id,
            regform_id=regform.id,
            enabled=(settings.enabled if settings else False),
            plans=[plan.serialize() for plan in plans],
            currency=regform.currency,
            base_price=float(regform.base_price),
            # What *this* person pays before a group plan is applied to it.
            # The same as the fee, unless another plugin has already taken
            # something off -- which is what a plugin that does so overwrites,
            # so that the picker quotes what its owner will really be charged.
            # `base_price` stays the standard fee, because that is what a plan's
            # own rate is worked out from when the discount applies to the fee.
            payer_base_price=float(regform.base_price),
            # Which of the two the plan's rate is worked out from, exactly as
            # `pricing.compute_discount` decides it on the server.
            applies_to=(settings.applies_to if settings else APPLIES_TO_BASE),
            disclaimer=(settings.disclaimer_text if settings else ''),
            allow_early_payment=(settings.allow_early_payment if settings else True),
        )

    def get_validators(self, existing_registration):
        from indico_group_registration.util import get_group_settings, get_plans, resolve_join_target

        def _validate(value):
            if not value:
                return
            mode = value.get('mode', MODE_NONE)
            if mode == MODE_NONE:
                return

            regform = self.form_item.registration_form
            settings = get_group_settings(regform)
            if settings is None or not settings.enabled:
                raise ValidationError(_('Group registration is not available for this form.'))

            if not value.get('accepted'):
                raise ValidationError(_('You must accept the group registration conditions.'))

            if mode == MODE_CREATE:
                plan = get_plan(get_plans(regform), value.get('plan'))
                if plan is None:
                    raise ValidationError(_('Unknown group plan.'))
                if not plan.is_group:
                    raise ValidationError(_('That plan is not a group plan.'))
                group_name = (value.get('name') or '').strip()
                if not group_name:
                    raise ValidationError(_('Please give your group a name.'))
                if len(group_name) > MAX_GROUP_NAME_LENGTH:
                    raise ValidationError(_('Group names are limited to {n} characters.')
                                          .format(n=MAX_GROUP_NAME_LENGTH))
            elif mode == MODE_JOIN:
                code = (value.get('code') or '').strip()
                if not code:
                    raise ValidationError(_('Please enter a group code.'))
                # A friendly, early "that group is full" -- the authoritative
                # check happens under a row lock when the registration is
                # actually created.
                _group, error = resolve_join_target(regform, code)
                if error:
                    raise ValidationError(error)

        return _validate

    def get_friendly_data(self, registration_data, for_humans=False, for_search=False):
        value = registration_data.data or {}
        mode = value.get('mode', MODE_NONE)
        if mode == MODE_NONE:
            return ''
        membership = registration_data.registration.group_membership
        if membership is not None:
            group = membership.group
            return f'{group.name} ({group.code})'
        # The chosen group could not be joined, or was dissolved afterwards.
        if mode == MODE_CREATE:
            return value.get('name', '')
        return value.get('code', '')

    def render_summary_data(self, data):
        membership = data.registration.group_membership
        if membership is None:
            return self.get_friendly_data(data, for_humans=True)
        group = membership.group
        plan = group.chosen_plan
        label = f'{group.name} ({group.code})'
        if plan is not None:
            label = f'{label} — {plan.label}'
        return label

    def render_invoice_data(self, data):
        return self.render_summary_data(data)

    def render_email_data(self, data):
        return self.render_summary_data(data)

    def render_reglist_column(self, data):
        text = self.get_friendly_data(data, for_humans=True)
        return RegistrationListColumn(text, text)


class GroupDiscountField(RegistrationFormFieldBase):
    """Carries the group discount as a named line on the invoice.

    The value is written by the plugin, never by a participant or a manager:
    the plugin locks the field through ``is_field_data_locked``, which makes
    core skip it when creating and modifying registrations.

    Note the asymmetry this class is built around.  ``calculate_price`` is
    handed only the stored value and the versioned data -- it cannot reach the
    registration, let alone the group -- so the amount has to be written into
    the value beforehand.  The ``render_*`` methods do get the whole
    ``RegistrationData``, so they can show the group as it stands right now.
    """

    name = DISCOUNT_FIELD
    mm_field_class = fields.Dict
    versioned_data_fields = frozenset()
    not_empty_if_required = False

    @property
    def default_value(self):
        return {}

    @property
    def empty_value(self):
        return {}

    def calculate_price(self, reg_data, versioned_data):
        if not reg_data:
            return Decimal(0)
        # Never let a malformed value turn into a positive charge.
        return min(_decimal(reg_data.get('amount', 0)), Decimal(0))

    def _describe(self, data):
        """The Value column: who the discount is for, and on what terms."""
        value = data.data or {}
        membership = data.registration.group_membership if data.registration else None

        if membership is not None:
            group = membership.group
            plan = group.pricing_plan
            parts = [group.name]
            if group.state.accepts_members:
                parts.append(_('{count} of {target} members')
                             .format(count=group.member_count, target=group.target_size))
            else:
                parts.append(_('{count} members').format(count=group.member_count))
            if plan is not None and plan.has_discount:
                parts.append(_('{value}% off').format(value=_format_number(plan.value))
                             if plan.type == 'percent' else plan.label)
            return ' · '.join(parts)

        # No membership left (dissolved, or the registration was detached).
        # Fall back to whatever the value recorded at the time.
        name = value.get('group_name') or value.get('group') or ''
        return str(name)

    def get_friendly_data(self, registration_data, for_humans=False, for_search=False):
        if not registration_data.data:
            return ''
        return self._describe(registration_data)

    def render_summary_data(self, data):
        return escape(self._describe(data))

    def render_invoice_data(self, data):
        return escape(self._describe(data))

    def render_email_data(self, data):
        return self._describe(data)

    def render_reglist_column(self, data):
        text = self._describe(data) if data.data else ''
        return RegistrationListColumn(text, text)

    def render_spreadsheet_data(self, data):
        return self._describe(data) if data.data else ''


def _format_number(value):
    """Render a Decimal without trailing zeroes (15 rather than 15.00).

    The `f` format is what keeps this readable: `Decimal('10.0').normalize()` is
    `Decimal('1E+1')`, and `str()` on that puts "1E+1% off" on the invoice.
    """
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return format(normalized.to_integral_value(), 'f')
    return format(normalized, 'f')
