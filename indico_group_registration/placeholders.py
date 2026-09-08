"""The figures a group reminder quotes, as e-mail placeholders.

The reminder an organizer sends is theirs to edit before it goes out, so the
wording is written in a dialog and only the numbers are ours.  One body is sent
to every member of every group still forming, which means a figure can only
reach the mail as a placeholder: core replaces them per recipient in
`RHRegistrationEmailRegistrants._send_emails`, and the same call renders the
preview.

Placeholders are registered per context for the whole instance, so these would
otherwise turn up in core's own *E-mail* action on every registration form
there is.  `plugin._get_email_placeholders` yields them only for a form that
has group registration switched on, which is the one filter the signal allows.
The `group_` prefix is the other half of that: two plugins claiming one name
makes `named_objects_from_signal` raise for *every* registration e-mail, not
only the ones that use the placeholder.
"""

from decimal import Decimal

from markupsafe import Markup

from indico.core.plugins import url_for_plugin
from indico.util.date_time import format_currency
from indico.util.i18n import _
from indico.util.placeholders import Placeholder

from indico_group_registration.plans import APPLIES_TO_BASE
from indico_group_registration.pricing import projected_price


class GroupPlaceholder(Placeholder):
    """A figure taken from the recipient's own group.

    `registration` is `None` while the dialog is only *describing* what is
    available, which is the one call that has nobody to quote.  A registration
    that is in no group renders empty rather than raising: these are offered to
    every e-mail sent from a group-enabled form, not only to the reminder.
    """

    @classmethod
    def render(cls, regform, registration):
        if registration is None:
            return ''
        member = registration.group_membership
        if member is None or member.group is None:
            return ''
        return cls.render_for(member, member.group, registration)

    @classmethod
    def render_for(cls, member, group, registration):
        raise NotImplementedError


def _projected_price(member, group, registration):
    """What this member would pay if the group were repriced as it stands.

    The plan is the one `reconcile.reconcile_group` would pick at the deadline,
    so the amount quoted is the amount that would be charged.
    """
    settings = group.settings
    applies_to = settings.applies_to if settings else APPLIES_TO_BASE
    return projected_price(registration, group.resolve_shortfall_plan(), applies_to)


class GroupNamePlaceholder(GroupPlaceholder):
    name = 'group_name'
    description = _("The name of the recipient's group")

    @classmethod
    def render_for(cls, member, group, registration):
        return group.name


class GroupCodePlaceholder(GroupPlaceholder):
    name = 'group_code'
    description = _('The code others type to join the group')

    @classmethod
    def render_for(cls, member, group, registration):
        return group.formatted_code


class GroupLinkPlaceholder(GroupPlaceholder):
    name = 'group_link'
    description = _('The link that joins the group')

    @classmethod
    def render_for(cls, member, group, registration):
        # `Markup`, like core's own `{link}`: `Placeholder.replace` escapes what
        # `render` hands back unless it is already markup, and this is a link
        # the reader has to be able to click.
        url = url_for_plugin('group_registration.join_link', group.registration_form,
                             join_uuid=group.join_uuid, _external=True)
        return Markup('<a href="{url}">{url}</a>').format(url=url)


class GroupPlanPlaceholder(GroupPlaceholder):
    name = 'group_plan'
    description = _('The rate the group is forming under')

    @classmethod
    def render_for(cls, member, group, registration):
        plan = group.chosen_plan
        return plan.label if plan else ''


class GroupFallbackPlanPlaceholder(GroupPlaceholder):
    name = 'group_fallback_plan'
    description = _('The rate the group qualifies for at its current size')

    @classmethod
    def render_for(cls, member, group, registration):
        # A group that qualifies for no plan at all falls back to the form's
        # standard rate, and the word has to fit the same sentence as a plan
        # label -- "the {group_fallback_plan} rate".
        plan = group.resolve_shortfall_plan()
        return plan.label if plan else str(_('standard'))


class GroupMembersPlaceholder(GroupPlaceholder):
    name = 'group_members'
    description = _('How many members the group has')

    @classmethod
    def render_for(cls, member, group, registration):
        return str(group.member_count)


class GroupTargetPlaceholder(GroupPlaceholder):
    name = 'group_target'
    description = _('How many members the group needs')

    @classmethod
    def render_for(cls, member, group, registration):
        return str(group.target_size)


class GroupSeatsLeftPlaceholder(GroupPlaceholder):
    name = 'group_seats_left'
    description = _('How many seats the group still has to fill')

    @classmethod
    def render_for(cls, member, group, registration):
        return str(group.seats_left)


class GroupDeadlinePlaceholder(GroupPlaceholder):
    name = 'group_deadline'
    description = _('When the group gets repriced if it has not filled')

    @classmethod
    def render_for(cls, member, group, registration):
        settings = group.settings
        if settings is None:
            return ''
        # Not formatted here: the confirmation mail quotes the same date when a
        # confirmed group can still fall back to forming, and the two have to
        # read alike.
        return settings.format_reconciliation_dt()


class GroupPricePlaceholder(GroupPlaceholder):
    name = 'group_price'
    description = _('What the recipient pays at the group rate')

    @classmethod
    def render_for(cls, member, group, registration):
        return format_currency(registration.price, registration.currency)


class GroupNewPricePlaceholder(GroupPlaceholder):
    name = 'group_new_price'
    description = _("What the recipient would pay at the group's current size")

    @classmethod
    def render_for(cls, member, group, registration):
        return format_currency(_projected_price(member, group, registration), registration.currency)


class GroupDifferencePlaceholder(GroupPlaceholder):
    name = 'group_difference'
    description = _('How much more that would be')

    @classmethod
    def render_for(cls, member, group, registration):
        projected = _projected_price(member, group, registration)
        difference = max(projected - registration.price, Decimal(0))
        return format_currency(difference, registration.currency)


class GroupBalancePlaceholder(GroupPlaceholder):
    name = 'group_balance'
    description = _('What the recipient would then still owe')

    @classmethod
    def render_for(cls, member, group, registration):
        # Against what they have actually handed over, not against the price:
        # somebody who paid early is the person this figure is for.
        projected = _projected_price(member, group, registration)
        return format_currency(max(projected - member.paid_amount, Decimal(0)), registration.currency)


#: Everything `plugin._get_email_placeholders` offers.  The tuple is the
#: authority: `tests/test_reminders.py` checks the default wording against it,
#: so a placeholder that is used but never registered fails the build instead
#: of reaching a participant's mailbox as literal braces.
GROUP_PLACEHOLDERS = (
    GroupNamePlaceholder,
    GroupCodePlaceholder,
    GroupLinkPlaceholder,
    GroupPlanPlaceholder,
    GroupFallbackPlanPlaceholder,
    GroupMembersPlaceholder,
    GroupTargetPlaceholder,
    GroupSeatsLeftPlaceholder,
    GroupDeadlinePlaceholder,
    GroupPricePlaceholder,
    GroupNewPricePlaceholder,
    GroupDifferencePlaceholder,
    GroupBalancePlaceholder,
)
