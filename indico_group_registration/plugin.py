"""The plugin class: everything this plugin hooks into, in one place."""

from pathlib import Path

from flask import session

from indico.core import signals
from indico.core.plugins import IndicoPlugin, get_plugin_template_module, url_for_plugin
from indico.modules.events.registration.fields.base import RegistrationFormFieldBase
from indico.modules.events.registration.views import (WPDisplayRegistrationFormConference,
                                                      WPDisplayRegistrationFormSimpleEvent, WPManageRegistration)
from indico.util.i18n import _
from indico.web.menu import SideMenuItem

from indico_group_registration.blueprint import blueprint
from indico_group_registration.fields import GroupDiscountField, GroupPlanField
from indico_group_registration.handlers import (get_locked_field_reason, handle_registration_created,
                                                handle_registration_deleted, handle_registration_state_updated)
from indico_group_registration.reglist import GroupListItem
from indico_group_registration.util import is_enabled


class GroupRegistrationPlugin(IndicoPlugin):
    """Group Registration

    Lets participants form their own registration groups and earn a
    plan-based discount.  A participant picks a group plan while registering,
    gets a code and a join link to share, and pays that plan's rate straight
    away.  The group confirms itself once the plan's seat count is filled; if
    it never fills, the plugin reprices it at the deadline and reports what
    each member still owes.

    Everything is configured per registration form, and nothing changes on a
    form until group registration is switched on for it.
    """

    configurable = False

    def init(self):
        super().init()

        # Field types.  `get_fields` is connected via the base class, which is
        # how core discovers every registration field implementation.
        self.connect(signals.core.get_fields, self._get_fields, sender=RegistrationFormFieldBase)

        # Registration lifecycle.
        self.connect(signals.event.registration_created, self._registration_created)
        self.connect(signals.event.registration_deleted, self._registration_deleted)
        self.connect(signals.event.registration_state_updated, self._registration_state_updated)
        self.connect(signals.event.is_field_data_locked, self._is_field_data_locked)

        # Management UI.
        self.connect(signals.event.registrant_list_items, self._registrant_list_items)
        self.connect(signals.menu.items, self._sidemenu_items, sender='event-management-sidemenu')

        # Participant and management page fragments.
        self.template_hook('before-render-registration-info', self._inject_group_panel)
        self.template_hook('extra-regform-settings', self._inject_regform_settings)

        # Our own template overrides live here; see `templates/core/` for what
        # is overridden and why.
        self.connect(signals.plugin.get_template_customization_paths, self._get_template_customization_paths)

        # The plan picker is a React component in the registration form.
        self.inject_bundle('main.js', WPDisplayRegistrationFormConference)
        self.inject_bundle('main.js', WPDisplayRegistrationFormSimpleEvent)
        self.inject_bundle('main.js', WPManageRegistration)
        self.inject_bundle('main.css', WPDisplayRegistrationFormConference)
        self.inject_bundle('main.css', WPDisplayRegistrationFormSimpleEvent)
        self.inject_bundle('main.css', WPManageRegistration)

    def get_blueprints(self):
        return blueprint

    # -- signal receivers ----------------------------------------------------

    def _get_fields(self, sender, **kwargs):
        yield GroupPlanField
        yield GroupDiscountField

    def _registration_created(self, registration, **kwargs):
        handle_registration_created(registration)

    def _registration_deleted(self, registration, **kwargs):
        handle_registration_deleted(registration)

    def _registration_state_updated(self, registration, **kwargs):
        handle_registration_state_updated(registration)

    def _is_field_data_locked(self, sender, registration=None, **kwargs):
        return get_locked_field_reason(sender, registration)

    def _registrant_list_items(self, sender, **kwargs):
        if is_enabled(sender):
            yield GroupListItem

    def _sidemenu_items(self, sender, event, **kwargs):
        if not event.can_manage(session.user, permission='registration'):
            return
        return SideMenuItem('group_registration', _('Group registration'),
                            url_for_plugin('group_registration.manage_overview', event),
                            section='organization', weight=-10)

    def _get_template_customization_paths(self, sender, **kwargs):
        """Override the checkout page so the group discount is named there too.

        This is the one core template the plugin forks.  It is a short, stable
        file, and the fork is limited to naming the discount in the sentence
        that tells the participant what they are about to pay.
        """
        return str(Path(__file__).parent / 'templates' / 'customization')

    # -- template hooks ------------------------------------------------------

    def _inject_group_panel(self, registration, from_management=False, **kwargs):
        """The 'Your group' panel above the registration summary."""
        if not is_enabled(registration.registration_form):
            return ''
        membership = registration.group_membership
        if membership is None:
            return ''
        tpl = get_plugin_template_module('_group_panel.html')
        return tpl.render_group_panel(membership=membership,
                                      group=membership.group,
                                      registration=registration,
                                      from_management=from_management)

    def _inject_regform_settings(self, regform, **kwargs):
        """A row in the registration form's settings box."""
        tpl = get_plugin_template_module('_regform_settings.html')
        return tpl.render_settings_row(regform=regform)
