"""A Group column in the management registrant list."""

import sqlalchemy as sa
from sqlalchemy.orm import joinedload

from indico.modules.events.registration.custom import CustomRegistrationListItem, RegistrationListColumn
from indico.modules.events.registration.models.registrations import Registration
from indico.util.i18n import L_, _

from indico_group_registration.models.groups import GroupState, RegistrationGroup
from indico_group_registration.models.members import GroupMember
from indico_group_registration.util import format_code


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
