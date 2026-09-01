"""The management registrant list: the Group column, and what the dialog
behind "Customize list" must not offer as one."""

import sqlalchemy as sa
from sqlalchemy.orm import joinedload

from indico.modules.events.registration.custom import CustomRegistrationListItem, RegistrationListColumn
from indico.modules.events.registration.models.registrations import Registration
from indico.util.i18n import L_, _

from indico_group_registration.constants import DISCOUNT_FIELD
from indico_group_registration.models.groups import GroupState, RegistrationGroup
from indico_group_registration.models.members import GroupMember
from indico_group_registration.util import find_field, format_code


class GroupListItem(CustomRegistrationListItem):
    """Which group a registrant is in, and how that group is doing."""

    name = 'group_registration_group'
    # A class attribute outlives any one request, so it must be lazy or it
    # would freeze whichever language happened to be active at import.
    title = L_('Group')

    @property
    def filter_choices(self):
        choices = {f'state:{state.name}': str(state.title) for state in GroupState}
        choices['none'] = _('Not in a group')
        return choices

    def modify_query(self, query, values):
        if not values:
            return query
        return (query
                .outerjoin(GroupMember, GroupMember.registration_id == Registration.id)
                .outerjoin(RegistrationGroup, RegistrationGroup.id == GroupMember.group_id))

    def get_filter_criterion(self, values):
        criteria = []
        if 'none' in values:
            criteria.append(GroupMember.id.is_(None))
        states = []
        for value in values:
            if not value.startswith('state:'):
                continue
            name = value[len('state:'):]
            if name in GroupState.__members__:
                states.append(GroupState[name])
        if states:
            criteria.append(RegistrationGroup.state.in_(states))
        if not criteria:
            return None
        return sa.or_(*criteria)

    def load_data(self, registrations):
        """One query for the whole page rather than one per row."""
        if not registrations:
            return {}
        by_id = {registration.id: registration for registration in registrations}
        members = (GroupMember.query
                   .filter(GroupMember.registration_id.in_(by_id))
                   .options(joinedload(GroupMember.group))
                   .all())

        data = {registration: RegistrationListColumn('', '') for registration in registrations}
        for member in members:
            registration = by_id.get(member.registration_id)
            if registration is None:
                continue
            group = member.group
            text = f'{group.name} ({format_code(group.code)})'
            data[registration] = RegistrationListColumn(text, text, td_attrs={'data-group-state': group.state.name})
        return data


# -- the "Customize list" dialog ---------------------------------------------
#
# The dialog offers every field on the form as a column an organizer can switch
# on, walking `regform.sections` straight from the template
# (`management/reglist_filter.html:102`), and core has no hook for leaving one
# out -- `RegistrationFormSection.available_fields` exists for that template and
# nothing else.  So the internal discount field was offered there under its
# manager-only section, as `Group discount (internal)` -> `Group discount`.  It
# is the plugin's own bookkeeping, and the `Group` column above is the one an
# organizer actually wants.
#
# Filtering it out happens in Flask's own `before_render_template`, which hands
# a receiver the context of the template about to be rendered.  Nothing in core
# is patched; two plugins doing this compose, because each only ever wraps what
# the other left behind; and if the template is ever restructured the worst that
# happens is the column comes back.
#
# Forking the template was the obvious alternative and is not an option: a
# customization path replaces a core template wholesale, so the second plugin to
# want this would silently lose to the first.

#: The template rendered by `RHRegistrationsListCustomize`.
REGLIST_FILTER_TEMPLATE = 'events/registration/management/reglist_filter.html'


class _SectionWithoutFields:
    """A section that does not admit to holding the fields we hide.

    Only `available_fields` is ours.  The dialog also reads `title` and
    `is_visible`, and anything else belongs to the section itself.
    """

    def __init__(self, section, hidden_ids):
        self._section = section
        self._hidden_ids = hidden_ids

    def __getattr__(self, name):
        return getattr(self._section, name)

    @property
    def available_fields(self):
        return [field for field in self._section.available_fields if field.id not in self._hidden_ids]


class _RegformWithoutFields:
    """The registration form as the column dialog is allowed to see it.

    Both section collections have to be wrapped: the dialog lists the enabled
    sections and, under a *Disabled sections* heading, the rest.
    """

    def __init__(self, regform, hidden_ids):
        self._regform = regform
        self._hidden_ids = hidden_ids

    def __getattr__(self, name):
        return getattr(self._regform, name)

    @property
    def sections(self):
        return [_SectionWithoutFields(section, self._hidden_ids) for section in self._regform.sections]

    @property
    def disabled_sections(self):
        return [_SectionWithoutFields(section, self._hidden_ids) for section in self._regform.disabled_sections]


def hide_internal_columns(context):
    """Take the internal discount field out of the column dialog's context.

    The template drops a section with no `available_fields` left of its own
    accord, which is what makes the whole manager-only section disappear rather
    than leaving an empty heading behind.
    """
    regform = context.get('regform')
    if regform is None:
        return
    field = find_field(regform, DISCOUNT_FIELD)
    if field is None:
        return
    hidden_ids = frozenset({field.id})
    context['regform'] = _RegformWithoutFields(regform, hidden_ids)
    # A column somebody had already switched on -- "Selection: All" was enough
    # to do it -- would otherwise stay on the list with nothing left to switch
    # it off.  Dropping it here means the next Apply also drops it from the
    # stored configuration.
    context['visible_items'] = [item for item in context.get('visible_items') or () if item not in hidden_ids]
