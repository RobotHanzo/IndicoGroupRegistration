"""The internal discount field must not be offered as a registrant-list column.

`management/reglist_filter.html` builds the "Customize list" dialog by walking
the form's sections and listing every field it finds, so the manager-only
section the plugin provisions for its invoice line showed up there as a column
an organizer could switch on.  `hide_internal_columns` takes it back out of the
template's context; these tests exercise the filtering against stand-ins,
because nothing here needs a database -- and they mimic the template's own
`if section.is_visible and section.available_fields` so that a section left
empty by the filtering really does disappear rather than leaving a heading.
"""

import pytest

from indico_group_registration.constants import DISCOUNT_FIELD, PLAN_FIELD
from indico_group_registration.reglist import _RegformWithoutFields, hide_internal_columns


class _Field:
    def __init__(self, id, input_type, is_deleted=False):
        self.id = id
        self.input_type = input_type
        self.is_deleted = is_deleted


class _Section:
    def __init__(self, title, fields, is_visible=True):
        self.title = title
        self.fields = list(fields)
        self.is_visible = is_visible

    @property
    def available_fields(self):
        return [field for field in self.fields if not field.is_deleted]


class _Regform:
    def __init__(self, sections, disabled_sections=()):
        self.title = 'Participants'
        self.sections = list(sections)
        self.disabled_sections = list(disabled_sections)

    @property
    def form_items(self):
        return [field for section in (*self.sections, *self.disabled_sections) for field in section.fields]


def _dialog_sections(regform):
    """The sections the dialog renders a heading and columns for."""
    return [section for section in regform.sections if section.is_visible and section.available_fields]


@pytest.fixture
def context():
    """A form with the two sections the plugin provisions, plus a real one."""
    personal = _Section('Personal data', [_Field(1, 'text'), _Field(2, 'email')])
    plan = _Section('Group registration', [_Field(3, PLAN_FIELD)])
    discount = _Section('Group discount (internal)', [_Field(4, DISCOUNT_FIELD)])
    return {'regform': _Regform([personal, plan, discount]), 'visible_items': [1, 4, 'state']}


def test_the_internal_section_disappears_entirely(context):
    hide_internal_columns(context)
    titles = [section.title for section in _dialog_sections(context['regform'])]
    assert titles == ['Personal data', 'Group registration']


def test_everything_else_is_still_offered(context):
    hide_internal_columns(context)
    offered = {field.id for section in _dialog_sections(context['regform']) for field in section.available_fields}
    assert offered == {1, 2, 3}


def test_a_column_already_switched_on_is_switched_off(context):
    hide_internal_columns(context)
    # Applying the dialog stores whatever is left here, so this is what drops
    # the column from a list somebody had already added it to.
    assert context['visible_items'] == [1, 'state']


def test_disabled_sections_are_filtered_too():
    discount = _Section('Group discount (internal)', [_Field(4, DISCOUNT_FIELD)], is_visible=False)
    regform = _Regform([], disabled_sections=[discount])
    context = {'regform': regform, 'visible_items': []}
    hide_internal_columns(context)
    # The template lists disabled sections separately, filtered the same way.
    assert not [s for s in context['regform'].disabled_sections if s.available_fields]


def test_a_form_without_the_field_is_left_alone():
    regform = _Regform([_Section('Personal data', [_Field(1, 'text')])])
    context = {'regform': regform, 'visible_items': [1]}
    hide_internal_columns(context)
    assert context['regform'] is regform
    assert context['visible_items'] == [1]


def test_unknown_attributes_reach_the_real_objects(context):
    hide_internal_columns(context)
    regform = context['regform']
    assert regform.title == 'Participants'
    assert [section.is_visible for section in regform.sections] == [True, True, True]


def test_two_plugins_filtering_the_same_dialog_compose(context):
    hide_internal_columns(context)
    # What a second plugin hiding a field of its own would do to our result.
    context['regform'] = _RegformWithoutFields(context['regform'], frozenset({3}))
    offered = {field.id for section in _dialog_sections(context['regform']) for field in section.available_fields}
    assert offered == {1, 2}
