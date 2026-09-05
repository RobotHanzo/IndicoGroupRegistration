"""The plugin class: everything this plugin hooks into, in one place."""

from importlib import import_module
from pathlib import Path

from flask import before_render_template, session

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
                                                handle_registration_deleted, handle_registration_state_updated,
                                                handle_registration_updated)
from indico_group_registration.placeholders import GROUP_PLACEHOLDERS
from indico_group_registration.reglist import REGLIST_FILTER_TEMPLATE, GroupListItem, hide_internal_columns
from indico_group_registration.util import get_switchable_plans, is_enabled


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
        self.connect(signals.event.registration_updated, self._registration_updated)
        self.connect(signals.event.registration_deleted, self._registration_deleted)
        self.connect(signals.event.registration_state_updated, self._registration_state_updated)
        self.connect(signals.event.is_field_data_locked, self._is_field_data_locked)

        # Management UI.
        self.connect(signals.event.registrant_list_items, self._registrant_list_items)
        self.connect(signals.menu.items, self._sidemenu_items, sender='event-management-sidemenu')

        # The organizer edits the reminder before it goes out, so the figures
        # in it reach the mail as placeholders core replaces per recipient.
        self.connect(signals.core.get_placeholders, self._get_email_placeholders, sender='registration-email')

        # The "Customize list" dialog builds its column list from the form
        # itself and core has no hook for leaving a field out, so the internal
        # discount field is filtered out of the template's context instead.
        # See `reglist.hide_internal_columns` for why that is Flask's signal.
        self.connect(before_render_template, self._before_render_template)

        # Participant and management page fragments.
        self.template_hook('before-render-registration-info', self._inject_group_panel)
        self.template_hook('extra-regform-settings', self._inject_regform_settings)

        # Our own template overrides live here; see `templates/core/` for what
        # is overridden and why.
        self.connect(signals.plugin.get_template_customization_paths, self._get_template_customization_paths)

        # Celery only sees tasks in modules that have actually been imported,
        # and nothing imports `tasks` on its own -- loading the plugin only
        # imports this module.  Without this the reconciliation task is never
        # registered and unfilled groups are never repriced.
        self.connect(signals.core.import_tasks, self._import_tasks)

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

    def _registration_updated(self, registration, **kwargs):
        handle_registration_updated(registration)

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

    def _get_email_placeholders(self, sender, regform=None, **kwargs):
        """Offer the group figures to every e-mail sent from a group-enabled form.

        Placeholders are registered per context for the whole instance, and the
        signal carries the form the mail is being written for -- which is the
        one chance to keep thirteen `{group_*}` names out of core's *E-mail*
        dialog on every other registration form there is.

        It has to answer the same way for a given form every time.  The dialog
        *describes* the placeholders on one call and *replaces* them on
        another, so a name offered by the first and missing from the second
        would go out as literal braces.

        Reading the setting is guarded because the rest of this is a generator,
        whose body does not run until the signal's result is iterated -- which
        is halfway through building somebody's mail, on a path this plugin does
        not own.
        """
        try:
            enabled = regform is not None and is_enabled(regform)
        except Exception:
            self.logger.exception('Could not decide whether to offer the group e-mail placeholders')
            return
        if enabled:
            yield from GROUP_PLACEHOLDERS

    def _before_render_template(self, sender, template=None, context=None, **kwargs):
        """Filter the registrant-list column dialog just before it renders.

        This receiver sees *every* template in the instance, so it does as
        little as possible before recognising its own, and it swallows whatever
        goes wrong: a column an organizer should not have been offered is worth
        far less than the page it is on.
        """
        if context is None or getattr(template, 'name', None) != REGLIST_FILTER_TEMPLATE:
            return
        try:
            hide_internal_columns(context)
        except Exception:
            self.logger.exception('Could not filter the registration list column dialog')

    def _import_tasks(self, sender, **kwargs):
        # Imported for its side effect: the module body is what registers the
        # periodic task with Celery.
        import_module('indico_group_registration.tasks')

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
                                      from_management=from_management,
                                      switchable_plans=get_switchable_plans(membership.group))

    def _inject_regform_settings(self, regform, **kwargs):
        """A row in the registration form's settings box."""
        tpl = get_plugin_template_module('_regform_settings.html')
        return tpl.render_settings_row(regform=regform)
