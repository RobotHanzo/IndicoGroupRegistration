"""The organizer's settings form."""

import json

from wtforms.fields import BooleanField, IntegerField, SelectField, TextAreaField
from wtforms.validators import NumberRange, Optional, ValidationError

from indico.util.i18n import _
from indico.web.forms.base import IndicoForm
from indico.web.forms.fields import IndicoDateTimeField
from indico.web.forms.widgets import SwitchWidget

from indico_group_registration.plans import APPLIES_TO_BASE, APPLIES_TO_TOTAL, PlanError, group_plans, parse_plans


PLANS_PLACEHOLDER = json.dumps([
    {'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': 'percent', 'value': 10},
    {'id': 'p10', 'label': 'Group of 10', 'size': 10, 'type': 'percent', 'value': 15},
], indent=2)


class GroupSettingsForm(IndicoForm):
    enabled = BooleanField(_('Enable group registration'), widget=SwitchWidget(),
                           description=_('Let participants create their own groups on this registration form. '
                                         'Existing registrations are not affected.'))

    plans = TextAreaField(_('Plans'),
                          description=_('A JSON list of plans. Each needs an "id", a "label" and a "size" (the '
                                        'number of seats), plus a "type" of "percent" or "amount" and a "value" '
                                        'if it carries a discount. The seat count is both the target and the cap: '
                                        'filling it confirms the group.'),
                          render_kw={'rows': 12, 'placeholder': PLANS_PLACEHOLDER})

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
                                                       'else was rejected after the group confirmed.'))

    disclaimer_text = TextAreaField(_('Disclaimer'), render_kw={'rows': 6},
                                    description=_('Shown next to the plan picker, and the participant must accept it. '
                                                  'Say what happens if the group does not fill. The version and the '
                                                  'time of acceptance are recorded against each membership.'))

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        if event is not None:
            self.reconciliation_dt.timezone = event.timezone

    def validate_plans(self, field):
        raw = (field.data or '').strip()
        if not raw:
            if self.enabled.data:
                raise ValidationError(_('Add at least one group plan before enabling group registration.'))
            field.parsed = ()
            return
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ValidationError(_('That is not valid JSON: {error}').format(error=exc))
        try:
            plans = parse_plans(parsed)
        except PlanError as exc:
            raise ValidationError(str(exc))
        if self.enabled.data and not group_plans(plans):
            raise ValidationError(_('At least one plan needs a seat count above 1, or no group can ever form.'))
        field.parsed = plans

    @property
    def parsed_plans(self):
        return getattr(self.plans, 'parsed', ())
