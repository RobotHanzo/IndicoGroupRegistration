"""Tests for the plan table's editor field.

The field sits between two shapes that do not match: what the widget hands back
(strings, an empty select) and what `plans.parse_plans` accepts.  These pin the
translation in both directions, and the one rule that really matters -- an
existing plan's id is never rewritten, because groups point at it.
"""

import json
from types import SimpleNamespace

import pytest
from wtforms import Form
from wtforms.validators import ValidationError

from indico_group_registration.forms import GroupPlansField, GroupSettingsForm


class PlansForm(Form):
    plans = GroupPlansField('Plans')


def make_field(stored=None):
    return PlansForm(data={'plans': stored}).plans


def submit(field, rows):
    field.process_formdata([json.dumps(rows)])
    return field.data


def validate(rows, *, enabled=True):
    """Run the form's own validator against a field holding `rows`."""
    field = make_field()
    field.data = rows
    form = SimpleNamespace(enabled=SimpleNamespace(data=enabled))
    GroupSettingsForm.validate_plans(form, field)
    return field.parsed


def test_stored_plans_become_rows():
    field = make_field([
        {'id': 'solo', 'label': 'Individual', 'size': 1, 'type': None, 'value': None},
        {'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': 'percent', 'value': 10.0},
    ])
    assert field.data == [
        {'id': 'solo', 'label': 'Individual', 'size': 1, 'type': '', 'value': ''},
        {'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': 'percent', 'value': 10.0},
    ]


def test_no_plans_is_no_rows():
    assert make_field(None).data == []
    assert make_field([]).data == []


def test_garbage_rows_are_dropped():
    # A hand-edited `plans` column should not take the settings page down.
    assert make_field(['nonsense', None, 42]).data == []


def test_existing_ids_survive_a_submit():
    field = make_field()
    rows = submit(field, [{'id': 'p3', 'label': 'Group of 3', 'size': '3', 'type': 'percent', 'value': '10'}])
    assert rows[0]['id'] == 'p3'


def test_new_rows_are_given_an_id():
    field = make_field()
    rows = submit(field, [{'label': 'Group of 5', 'size': '5', 'type': '', 'value': ''}])
    assert rows[0]['id'].startswith('plan-')


def test_new_ids_are_distinct():
    field = make_field()
    rows = submit(field, [
        {'label': 'A', 'size': '2', 'type': '', 'value': ''},
        {'label': 'B', 'size': '3', 'type': '', 'value': ''},
    ])
    assert rows[0]['id'] != rows[1]['id']


def test_rows_parse_into_plans():
    plans = validate([
        {'id': 'p5', 'label': 'Group of 5', 'size': 5, 'type': 'amount', 'value': '25'},
        {'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': 'percent', 'value': '10'},
    ])
    # `parse_plans` orders by seat count, smallest first.
    assert [plan.id for plan in plans] == ['p3', 'p5']
    assert plans[0].value == 10
    assert plans[1].type == 'amount'


def test_empty_discount_columns_mean_no_discount():
    plans = validate([{'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': '', 'value': ''}], enabled=True)
    assert plans[0].type is None
    assert not plans[0].has_discount


def test_enabling_without_a_group_plan_is_rejected():
    with pytest.raises(ValidationError):
        validate([{'id': 'solo', 'label': 'Individual', 'size': 1, 'type': '', 'value': ''}], enabled=True)


def test_a_single_seat_plan_is_fine_while_disabled():
    plans = validate([{'id': 'solo', 'label': 'Individual', 'size': 1, 'type': '', 'value': ''}], enabled=False)
    assert [plan.id for plan in plans] == ['solo']


def test_enabling_with_no_plans_is_rejected():
    with pytest.raises(ValidationError):
        validate([], enabled=True)


def test_no_plans_is_fine_while_disabled():
    assert validate([], enabled=False) == ()


def test_a_bad_row_becomes_a_validation_error():
    with pytest.raises(ValidationError):
        validate([{'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': 'percent', 'value': '150'}])
