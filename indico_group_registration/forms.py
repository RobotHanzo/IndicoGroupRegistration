"""The organizer's settings form."""

from uuid import uuid4

from wtforms.fields import BooleanField, IntegerField, SelectField, TextAreaField
from wtforms.validators import NumberRange, Optional, ValidationError

from indico.util.i18n import _
from indico.web.forms.base import IndicoForm
from indico.web.forms.fields import IndicoDateTimeField, MultipleItemsField
from indico.web.forms.widgets import SwitchWidget

from indico_group_registration.plans import (AMOUNT, APPLIES_TO_BASE, APPLIES_TO_TOTAL, MAX_PLAN_SIZE, PERCENT,
                                             PlanError, group_plans, parse_plans)


def _new_plan_id():
    """A short, opaque id for a newly added plan.

    Groups store the id of the plan they were created under, so it has to
    survive an organizer renaming, reordering or repricing the row it came
    from -- which is why it is generated rather than typed.
    """
    return f'plan-{uuid4().hex[:8]}'


def _coerce_seats(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(_('The seat count must be a whole number.'))


class GroupPlansField(MultipleItemsField):
    """The plan table, as one row per plan.

    ``id`` is carried as an opaque key: the widget hands back whatever id a row
    already had and leaves a new row without one, so `process_formdata` is the
    only place a plan is ever given an id.  Nothing an organizer types can
    change the id of an existing plan, which is what keeps groups attached to
    the plan they were created under.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('fields', [
            {'id': 'label', 'caption': _('Plan'), 'type': 'text', 'required': True},
            {'id': 'size', 'caption': _('Seats'), 'type': 'number', 'required': True,
             'min': 1, 'max': MAX_PLAN_SIZE, 'step': 1, 'coerce': _coerce_seats},
            {'id': 'type', 'caption': _('Discount'), 'type': 'select'},
            {'id': 'value', 'caption': _('Off'), 'type': 'number', 'min': 0, 'step': 0.01},
        ])
        kwargs.setdefault('choices', {'type': {PERCENT: _('% off'), AMOUNT: _('fixed amount off')}})
        kwargs.setdefault('uuid_field', 'id')
        kwargs.setdefault('uuid_field_opaque', True)
        kwargs.setdefault('sortable', True)
        kwargs.setdefault('unique_field', 'label')
        super().__init__(*args, **kwargs)

    def process_data(self, value):
        """Turn stored plans into rows the widget can render.

        A plan with no discount is stored with ``None`` for its type and value;
        the widget's ``<select>`` and number input want an empty string, and a
        literal ``None`` would be printed into the table as text.
        """
        super().process_data(value)
        self.data = [{
            'id': plan.get('id') or '',
            'label': plan.get('label') or '',
            'size': plan.get('size') or '',
            'type': plan.get('type') or '',
            'value': '' if plan.get('value') in (None, '') else plan['value'],
        } for plan in (self.data or []) if isinstance(plan, dict)]

    def process_formdata(self, valuelist):
        super().process_formdata(valuelist)
        for row in (self.data or []):
            if not row.get('id'):
                row['id'] = _new_plan_id()


class GroupSettingsForm(IndicoForm):
    enabled = BooleanField(_('Enable group registration'), widget=SwitchWidget(),
                           description=_('Let participants create their own groups on this registration form. '
                                         'Existing registrations are not affected.'))

    plans = GroupPlansField(_('Plans'),
                            description=_('One row per plan. The seat count is both the target and the cap: filling '
                                          'it confirms the group and closes it. A plan with a single seat never '
                                          'forms a group, so leave those out unless you want the standard rate '
                                          'listed. Leave the discount empty for a plan that carries none.'))

    applies_to = SelectField(_('Discount applies to'),
                             choices=[(APPLIES_TO_BASE, _('The registration fee only')),
                                      (APPLIES_TO_TOTAL, _('The whole price, including paid options'))],
                             description=_('Choosing the registration fee keeps paid extras such as the dinner at '
                                           'full price, which is usually what an organizer can explain.'))

    reconciliation_dt = IndicoDateTimeField(_('Reconciliation deadline'), [Optional()], allow_clear=True,
                                            description=_('When groups that never filled are repriced. Leave empty '
                                                          'to use the date registration closes. Set it well before '
                                                          'the event: members who already paid will owe a balance, '
                                                          'and somebody has to collect it.'))

    allow_early_payment = BooleanField(_('Allow paying before the group fills'), widget=SwitchWidget(),
                                       description=_('When off, the plan picker warns that a group must fill before '
                                                     'anyone pays. Leave it on unless you would rather not chase '
                                                     'balances.'))

    max_groups_per_user = IntegerField(_('Groups per person'), [NumberRange(min=0)],
                                       description=_('How many groups one person may create on this form. '
                                                     '0 means no limit.'))

    count_pending = BooleanField(_('Count registrations awaiting approval'), widget=SwitchWidget(),
                                 description=_('Whether a member still awaiting moderation fills a seat. '
                                               'Withdrawn and rejected registrations never do.'))

    revoke_on_member_loss = BooleanField(_('Reprice a confirmed group that loses a member'), widget=SwitchWidget(),
                                         description=_('Off by default: nobody should be rebilled because somebody '
                                                       'else was rejected after the group confirmed. On, such a '
                                                       'group goes back to forming, and the plan picker, the '
                                                       'confirmation e-mail, the group panel and the checkout all '
                                                       'stop telling members their amount is final.'))

    disclaimer_text = TextAreaField(_('Disclaimer'), render_kw={'rows': 6},
                                    description=_('Shown next to the plan picker, and the participant must accept it. '
                                                  'Say what happens if the group does not fill. The version and the '
                                                  'time of acceptance are recorded against each membership.'))

    def __init__(self, *args, event=None, **kwargs):
        # `IndicoDateTimeField.timezone` is a read-only property: it reads
        # `self.get_form().timezone` and only falls back to the *user's* timezone
        # when the form has none.  So the timezone is handed over as a plain
        # attribute on the form, before the fields are bound -- assigning to the
        # field's property raises `AttributeError`.
        if event is not None:
            self.timezone = event.timezone
        super().__init__(*args, **kwargs)

    def validate_plans(self, field):
        rows = field.data or []
        if not rows:
            if self.enabled.data:
                raise ValidationError(_('Add at least one group plan before enabling group registration.'))
            field.parsed = ()
            return
        try:
            plans = parse_plans([{
                'id': row.get('id'),
                'label': row.get('label'),
                'size': row.get('size'),
                'type': row.get('type') or None,
                'value': row.get('value') or 0,
            } for row in rows])
        except PlanError as exc:
            raise ValidationError(str(exc))
        if self.enabled.data and not group_plans(plans):
            raise ValidationError(_('At least one plan needs a seat count above 1, or no group can ever form.'))
        field.parsed = plans

    @property
    def parsed_plans(self):
        return getattr(self.plans, 'parsed', ())
