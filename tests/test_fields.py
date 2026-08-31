"""Tests for the discount field's price reading.

`calculate_price` receives only the stored value -- never the registration --
so its whole job is to read a number out of JSON without ever raising and
without ever turning into a charge.
"""

from decimal import Decimal

import pytest

from indico_group_registration.fields import GroupDiscountField


@pytest.fixture
def field():
    # `calculate_price` never touches the form item.
    return GroupDiscountField(form_item=None)


@pytest.mark.parametrize(('value', 'expected'), (
    ({'amount': '-37.50'}, Decimal('-37.50')),
    ({'amount': -37.5}, Decimal('-37.5')),
    ({'amount': '-0.01'}, Decimal('-0.01')),
    ({'amount': '0'}, Decimal(0)),
    ({}, Decimal(0)),
    (None, Decimal(0)),
    ({'group': 'ABCD2345'}, Decimal(0)),
))
def test_calculate_price(field, value, expected):
    assert field.calculate_price(value, {}) == expected


@pytest.mark.parametrize('value', (
    {'amount': '37.50'},
    {'amount': 100},
))
def test_never_charges(field, value):
    """A malformed positive amount must not become a surcharge."""
    assert field.calculate_price(value, {}) == Decimal(0)


@pytest.mark.parametrize('value', (
    {'amount': 'free'},
    {'amount': None},
    {'amount': ['nope']},
    {'amount': {'a': 1}},
))
def test_garbage_is_zero_not_an_exception(field, value):
    assert field.calculate_price(value, {}) == Decimal(0)


def test_default_value_is_empty(field):
    assert field.default_value == {}
    assert field.empty_value == {}
