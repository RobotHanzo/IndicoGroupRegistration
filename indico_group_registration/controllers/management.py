"""Organizer-facing endpoints."""

from flask import flash, redirect, request, session
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import NotFound

from indico.core.db import db
from indico.core.plugins import WPJinjaMixinPlugin, url_for_plugin
from indico.modules.events.registration.controllers.management import RHManageRegFormBase, RHManageRegFormsBase
from indico.modules.events.registration.controllers.management.reglists import (RHRegistrationEmailRegistrants,
                                                                                RHRegistrationEmailRegistrantsPreview)
from indico.modules.events.registration.forms import EmailRegistrantsForm
from indico.modules.events.registration.models.form_fields import RegistrationFormFieldData
from indico.modules.events.registration.models.forms import RegistrationForm
from indico.modules.events.registration.models.registrations import Registration, RegistrationData
from indico.modules.events.registration.views import WPManageRegistration
from indico.modules.logs import EventLogRealm, LogKind
from indico.util.i18n import _, ngettext
from indico.web.forms.base import FormDefaults
from indico.web.util import jsonify_data, jsonify_template

from indico_group_registration.forms import GroupSettingsForm
from indico_group_registration.models.groups import GroupState, RegistrationGroup
from indico_group_registration.models.members import GroupMember
from indico_group_registration.models.settings import GroupSettings
from indico_group_registration.notifications import notify_group_dissolved
from indico_group_registration.operations import dissolve_group
from indico_group_registration.pricing import pricing_in_progress, sync_balance_state
from indico_group_registration.reconcile import reconcile_group
from indico_group_registration.reminders import default_body, default_subject
from indico_group_registration.util import get_group_settings, provision_fields


#: The wording an organizer starts from.  It names the three consequences that
#: make the group rate enforceable: the fallback rate, the balance, and the
#: possibility of being refused entry.
DEFAULT_DISCLAIMER = _(
    'This is a group rate. It applies only if your group reaches the number of members its plan requires by the '
    'registration deadline. If the group is short, you will be charged the rate your group does qualify for -- up '
    'to the standard rate -- and you will be asked to pay the difference even if you have already paid. '
    'Registrations with an unpaid balance may be cancelled, and entry to the event may be refused.'
)


class WPGroupRegistration(WPJinjaMixinPlugin, WPManageRegistration):
    """Renders our management pages inside the registration management area.

    ``WPJinjaMixinPlugin`` has to come first.  ``WPManageRegistration`` sets
    ``template_prefix = 'events/registration/'`` for core templates, and that
    prefix is prepended verbatim -- turning ``group_registration:overview.html``
    into ``events/registration/group_registration:overview.html``, which exists
    nowhere.  Listing the mixin first clears the prefix and swaps in the plugin
    template loader, so ``group_registration:<name>`` resolves to this plugin's
    ``templates/`` directory.
    """

    sidemenu_option = 'group_registration'


class RHGroupOverview(RHManageRegFormsBase):
    """Every registration form in the event, and whether groups are on."""

    def _process(self):
        regforms = (RegistrationForm.query
                    .with_parent(self.event)
                    .filter(~RegistrationForm.is_deleted)
                    .order_by(RegistrationForm.title)
                    .all())
        rows = []
        for regform in regforms:
            settings = get_group_settings(regform)
            rows.append({
                'regform': regform,
                'settings': settings,
                'group_count': (RegistrationGroup.query
                                .filter(RegistrationGroup.registration_form_id == regform.id,
                                        RegistrationGroup.state != GroupState.dissolved)
                                .count()) if settings else 0,
            })
        return WPGroupRegistration.render_template('group_registration:overview.html', self.event, rows=rows)


class RHGroupRegFormBase(RHManageRegFormBase):
    """Base for pages scoped to one registration form."""

    def _process_args(self):
        RHManageRegFormBase._process_args(self)
        self.settings = get_group_settings(self.regform)


class RHGroupSettings(RHGroupRegFormBase):
    """Configure plans, the deadline and the disclaimer."""

    def _process(self):
        settings = self.settings
        defaults = FormDefaults(
            enabled=settings.enabled if settings else False,
            plans=(settings.plans if settings and settings.plans else []),
            applies_to=settings.applies_to if settings else 'base',
            reconciliation_dt=settings.reconciliation_dt if settings else None,
            allow_early_payment=settings.allow_early_payment if settings else True,
            max_groups_per_user=settings.max_groups_per_user if settings else 1,
            count_pending=settings.count_pending if settings else True,
            revoke_on_member_loss=settings.revoke_on_member_loss if settings else False,
            disclaimer_text=settings.disclaimer_text if settings else DEFAULT_DISCLAIMER,
        )
        form = GroupSettingsForm(obj=defaults, event=self.event)

        if form.validate_on_submit():
            if settings is None:
                settings = GroupSettings(registration_form=self.regform)
                db.session.add(settings)

            old_disclaimer = settings.disclaimer_text
            settings.enabled = form.enabled.data
            settings.plans = [plan.serialize() for plan in form.parsed_plans]
            settings.applies_to = form.applies_to.data
            settings.reconciliation_dt = form.reconciliation_dt.data
            settings.allow_early_payment = form.allow_early_payment.data
            settings.max_groups_per_user = form.max_groups_per_user.data
            settings.count_pending = form.count_pending.data
            settings.revoke_on_member_loss = form.revoke_on_member_loss.data
            settings.disclaimer_text = form.disclaimer_text.data or ''

            if settings.disclaimer_text != old_disclaimer:
                # A new version, so that memberships recorded from now on point
                # at the wording those people actually saw.
                settings.disclaimer_version = (settings.disclaimer_version or 0) + 1

            if settings.enabled:
                provision_fields(self.regform)

            db.session.flush()
            self.event.log(EventLogRealm.management, LogKind.change, 'Registration',
                           f'Group registration settings for "{self.regform.title}"', session.user)
            db.session.commit()
            flash(_('Group registration settings saved.'), 'success')
            return redirect(url_for_plugin('group_registration.manage_settings', self.regform))

        return WPGroupRegistration.render_template('group_registration:settings.html', self.event,
                                                   regform=self.regform, form=form, settings=settings)


def _load_groups(regform):
    """The groups on a form with their members, newest first.

    Members and registrations come along in the same query: the page computes
    every group's seat count, which reads a registration's state per member.
    """
    return (RegistrationGroup.query
            .filter_by(registration_form_id=regform.id)
            .options(joinedload(RegistrationGroup.members).joinedload(GroupMember.registration))
            .order_by(RegistrationGroup.created_dt.desc())
            .all())


def _forming_group_members(regform):
    """Every live registration that belongs to a group still forming.

    Confirmed, short and dissolved groups cannot fall short any more, so their
    members have nothing left to be reminded about.  `is_active` is core's own
    definition -- not deleted, not rejected, not withdrawn -- which is the same
    rule the rest of the plugin's mail keeps to.

    Ordered the way core orders the registrant list, which is what decides
    whose registration the *Preview email* button quotes the mail against.

    Everything the `{group_*}` placeholders read is eager-loaded, because they
    are rendered once per recipient: the price alone walks three relationships
    deep, and the group and the transaction are each a fresh query per person
    otherwise.
    """
    return (Registration.query
            .join(GroupMember, GroupMember.registration_id == Registration.id)
            .join(RegistrationGroup, RegistrationGroup.id == GroupMember.group_id)
            .filter(RegistrationGroup.registration_form_id == regform.id,
                    RegistrationGroup.state == GroupState.forming,
                    Registration.is_active)
            .options(joinedload(Registration.data)
                     .joinedload(RegistrationData.field_data)
                     .joinedload(RegistrationFormFieldData.field),
                     joinedload(Registration.transaction),
                     joinedload(Registration.group_membership).joinedload(GroupMember.group))
            .order_by(*Registration.order_by_name)
            .all())


class RHManageGroups(RHGroupRegFormBase):
    """Every group on this form."""

    def _process(self):
        groups = _load_groups(self.regform)
        # The count is what decides whether the reminder button is live; the
        # dialog it opens quotes the deadline itself.
        forming_count = sum(group.state == GroupState.forming for group in groups)
        return WPGroupRegistration.render_template('group_registration:groups.html', self.event,
                                                   regform=self.regform, groups=groups, settings=self.settings,
                                                   forming_count=forming_count)


class _ReminderMixin:
    """What both reminder endpoints have to establish before they do anything.

    The recipients are *found*, never read off the request.  `_process_args` on
    core's e-mail handlers is where a submitted `registration_id` list becomes
    the list of people written to, and skipping it is the whole point: the
    button is one click, and no request can talk this endpoint into writing to
    somebody who is not in a group that is still forming.

    Rebuilding the list on the submit as well as on the open is deliberate and
    not just tidiness -- a group that filled while the dialog sat open is
    dropped, and so is a member who withdrew.
    """

    def _process_args(self):
        # Not `RHGroupRegFormBase`: these two inherit from core's e-mail
        # handlers instead, so the one line that base class adds is repeated
        # here rather than borrowed from a class that is not in the MRO.
        RHManageRegFormBase._process_args(self)
        self.settings = get_group_settings(self.regform)
        if self.settings is None:
            raise NotFound(_('Group registration is not set up on this registration form.'))
        self.registrations = self._find_recipients()

    def _find_recipients(self):
        raise NotImplementedError


class RHRemindFormingGroups(_ReminderMixin, RHRegistrationEmailRegistrants):
    """Tell every member of every group still forming what falling short would cost.

    This is the one e-mail an organizer sends by hand, and it keeps to the rule
    the rest of the plugin's mail follows: it reaches only the members of a
    group, with their own group's figures, and nothing a participant can do
    triggers it.

    The wording is the organizer's.  Core's own *E-mail* action already does
    every hard part of that -- the rich text editor, the placeholders, the
    event locale, the sender addresses an organizer is allowed to use, the
    preview, the log entry -- so this subclasses it and changes the two things
    that matter: who the mail goes to, and what the dialog opens with.  The
    figures that used to be baked into a Jinja template reach the mail as
    `{group_*}` placeholders instead, which is what lets an organizer rewrite
    the text around them without losing them.
    """

    def _find_recipients(self):
        return _forming_group_members(self.regform)

    def _process(self):
        if not self.registrations:
            # Reachable: the button is only enabled while something is forming,
            # but a page that has been open a while has an old count on it.
            return jsonify_template('group_registration:remind_forming_groups.html', form=None,
                                    regform=self.regform, event=self.event, count=0, group_count=0, deadline=None)

        with self.event.force_event_locale():
            subject, body = default_subject(), default_body()
        form = EmailRegistrantsForm(subject=subject, body=body, regform=self.regform,
                                    registration_id=[r.id for r in self.registrations],
                                    recipients=[r.email for r in self.registrations])
        # A reminder that a group has not filled is not a ticket delivery, and
        # offering the switch would put an attachment for the amount that is
        # about to change on the one mail that says it is about to change.
        del form.attach_ticket

        group_count = (RegistrationGroup.query
                       .filter_by(registration_form_id=self.regform.id, state=GroupState.forming)
                       .count())

        if form.validate_on_submit():
            self._send_emails(form)
            count = len(self.registrations)
            self.event.log(EventLogRealm.management, LogKind.other, 'Registration',
                           f'Reminded {count} member(s) of {group_count} forming group(s) '
                           f'on "{self.regform.title}"', session.user)
            db.session.commit()
            flash(ngettext('A reminder was sent to one group member.',
                           'A reminder was sent to {n} group members.', count).format(n=count), 'success')
            return jsonify_data()

        return jsonify_template('group_registration:remind_forming_groups.html', form=form, regform=self.regform,
                                event=self.event, count=len(self.registrations), group_count=group_count,
                                deadline=self.settings.get_reconciliation_dt())


class RHRemindFormingGroupsPreview(_ReminderMixin, RHRegistrationEmailRegistrantsPreview):
    """Render the reminder as the first member on the list will read it.

    Core's preview endpoint would have done, but the *Preview email* button is
    wired to it by core's own JavaScript, which quotes the mail against
    ``getSelectedRows()[0]`` -- and nothing is selected here, because the whole
    point of the button is that nobody had to select anything.  Rather than
    ship JavaScript to work around that, this picks the registration the JS
    could not: the first member of a group still forming.
    """

    def _find_recipients(self):
        return _forming_group_members(self.regform)[:1]


class RHGroupBalances(RHGroupRegFormBase):
    """Members whose price rose above what they already paid.

    Indico has no partial-payment concept, so this list is the collection tool:
    it says who owes what, and the amounts have to be taken at the desk or by
    transfer and recorded as a manual transaction.
    """

    def _process(self):
        members = (GroupMember.query
                   .join(GroupMember.group)
                   .filter(RegistrationGroup.registration_form_id == self.regform.id)
                   .options(joinedload(GroupMember.registration), joinedload(GroupMember.group))
                   .all())
        rows = [member for member in members if member.balance_due > 0]
        rows.sort(key=lambda m: m.balance_due, reverse=True)
        total = sum((member.balance_due for member in rows), start=0)
        return WPGroupRegistration.render_template('group_registration:balances.html', self.event,
                                                   regform=self.regform, rows=rows, total=total)


class RHRefreshBalanceStates(RHGroupRegFormBase):
    """Put every member's payment state back in step with what they owe.

    The plugin does this whenever it reprices somebody, so this exists for the
    memberships repriced before the plugin knew how to: it is the one action
    that fixes a registration still showing as settled while a balance is open.
    """

    def _process(self):
        members = (GroupMember.query
                   .join(GroupMember.group)
                   .filter(RegistrationGroup.registration_form_id == self.regform.id)
                   .options(joinedload(GroupMember.registration), joinedload(GroupMember.group))
                   .all())
        changed = 0
        with pricing_in_progress():
            for member in members:
                registration = member.registration
                if registration is None or registration.is_deleted:
                    continue
                before = registration.state
                sync_balance_state(registration)
                changed += registration.state != before
        db.session.commit()
        if changed:
            flash(ngettext('One registration was moved to "awaiting payment" or back.',
                           '{n} registrations were moved to "awaiting payment" or back.',
                           changed).format(n=changed), 'success')
        else:
            flash(_('Every registration already shows the right payment state.'), 'info')
        return jsonify_data(flash=False)


class RHGroupActionBase(RHGroupRegFormBase):
    # The base class normalizes against the registration form's locator, which
    # has no `group_id`.  The extra view arg then looks like a mismatch, and
    # normalization answers a POST with 404 -- so normalize against the group
    # itself, whose locator is the form's plus `group_id`.
    normalize_url_spec = {
        'locators': {lambda self: self.group}
    }

    def _process_args(self):
        RHGroupRegFormBase._process_args(self)
        self.group = (RegistrationGroup.query
                      .filter_by(id=request.view_args['group_id'], registration_form_id=self.regform.id)
                      .first())
        if self.group is None:
            raise NotFound


class RHDissolveGroup(RHGroupActionBase):
    """Take a group apart and put everyone back on the standard rate."""

    def _process(self):
        if self.group.state == GroupState.dissolved:
            return jsonify_data(flash=False)
        dissolve_group(self.group)
        notify_group_dissolved(self.group)
        self.event.log(EventLogRealm.management, LogKind.negative, 'Registration',
                       f'Dissolved group "{self.group.name}" ({self.group.code})', session.user)
        db.session.commit()
        flash(_('The group has been dissolved and every member is back on the standard rate.'), 'success')
        return jsonify_data(flash=False)


class RHReconcileGroup(RHGroupActionBase):
    """Reprice one group now, without waiting for the deadline."""

    def _process(self):
        result = reconcile_group(self.group)
        if result is None:
            db.session.rollback()
            flash(_('That group is not forming, so there is nothing to reprice.'), 'warning')
            return jsonify_data(flash=False)
        self.event.log(EventLogRealm.management, LogKind.change, 'Registration',
                       f'Reconciled group "{self.group.name}" ({self.group.code})', session.user)
        db.session.commit()
        flash(_('The group has been repriced.'), 'success')
        return jsonify_data(flash=False)
