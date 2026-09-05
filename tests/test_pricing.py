"""Tests for the money arithmetic that does not need a database.

These use stand-ins for `Registration` and `RegistrationData`: `compute_discount`
only ever touches `base_price` and the prices of the data rows, and pinning that
down is exactly the point.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from indico_group_registration import pricing
from indico_group_registration.plans import APPLIES_TO_BASE, APPLIES_TO_TOTAL, Plan


PLAN_10 = Plan(id='p10', label='Group of 10', size=10, type='percent', value=Decimal(15))
PLAN_FIXED = Plan(id='p25', label='Group of 25', size=25, type='amount', value=Decimal(50))
PLAN_NO_DISCOUNT = Plan(id='solo', label='Individual', size=1)


def make_registration(base_price, items=(), price_adjustment=0):
    return SimpleNamespace(base_price=Decimal(str(base_price)), data=list(items),
                           price_adjustment=Decimal(str(price_adjustment)))


def item(price):
    return SimpleNamespace(price=Decimal(str(price)))


@pytest.fixture
def no_discount_row(monkeypatch):
    monkeypatch.setattr(pricing, 'get_discount_data', lambda registration: None)


class TestComputeDiscount:
    def test_percent_of_base(self, no_discount_row):
        registration = make_registration('250.00')
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_BASE) == Decimal('-37.50')

    def test_base_mode_ignores_paid_extras(self, no_discount_row):
        registration = make_registration('250.00', [item('80.00')])
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_BASE) == Decimal('-37.50')

    def test_total_mode_includes_paid_extras(self, no_discount_row):
        registration = make_registration('250.00', [item('80.00')])
        # 15% of 330.00
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_TOTAL) == Decimal('-49.50')

    def test_fixed_amount(self, no_discount_row):
        registration = make_registration('250.00')
        assert pricing.compute_discount(registration, PLAN_FIXED, APPLIES_TO_BASE) == Decimal('-50.00')

    def test_plan_without_discount(self, no_discount_row):
        registration = make_registration('250.00')
        assert pricing.compute_discount(registration, PLAN_NO_DISCOUNT, APPLIES_TO_BASE) == Decimal(0)

    def test_no_plan(self, no_discount_row):
        registration = make_registration('250.00')
        assert pricing.compute_discount(registration, None, APPLIES_TO_BASE) == Decimal(0)

    def test_free_registration(self, no_discount_row):
        registration = make_registration('0.00')
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_BASE) == Decimal(0)

    def test_null_base_price(self, no_discount_row):
        registration = SimpleNamespace(base_price=None, data=[])
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_BASE) == Decimal(0)

    def test_our_own_discount_row_is_excluded(self, monkeypatch):
        """The discount must not be computed from a price that already has it.

        Recomputing an existing discount is the obvious way to get a slowly
        shrinking price every time a group changes, so this is pinned.
        """
        discount_row = item('-37.50')
        extra = item('80.00')
        registration = make_registration('250.00', [discount_row, extra])
        monkeypatch.setattr(pricing, 'get_discount_data', lambda reg: discount_row)

        # 15% of (250 + 80), not of (250 + 80 - 37.50).
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_TOTAL) == Decimal('-49.50')

    def test_repeated_application_is_stable(self, monkeypatch):
        discount_row = item('-37.50')
        registration = make_registration('250.00', [discount_row])
        monkeypatch.setattr(pricing, 'get_discount_data', lambda reg: discount_row)
        first = pricing.compute_discount(registration, PLAN_10, APPLIES_TO_TOTAL)
        discount_row.price = first
        second = pricing.compute_discount(registration, PLAN_10, APPLIES_TO_TOTAL)
        assert first == second == Decimal('-37.50')

    def test_item_with_no_price(self, no_discount_row):
        registration = make_registration('250.00', [SimpleNamespace(price=None)])
        assert pricing.compute_discount(registration, PLAN_10, APPLIES_TO_TOTAL) == Decimal('-37.50')


class TestProjectedPrice:
    """The figure the reminder quotes has to be the figure the invoice would show."""

    def test_no_plan_is_the_full_price(self, no_discount_row):
        registration = make_registration('250.00', [item('80.00')])
        assert pricing.projected_price(registration, None, APPLIES_TO_BASE) == Decimal('330.00')

    def test_percent_plan_off_the_base(self, no_discount_row):
        registration = make_registration('250.00', [item('80.00')])
        # 250 - 15% of 250, plus the dinner at full price.
        assert pricing.projected_price(registration, PLAN_10, APPLIES_TO_BASE) == Decimal('292.50')

    def test_percent_plan_off_the_total(self, no_discount_row):
        registration = make_registration('250.00', [item('80.00')])
        # 330 - 15% of 330.
        assert pricing.projected_price(registration, PLAN_10, APPLIES_TO_TOTAL) == Decimal('280.50')

    def test_replaces_the_discount_already_written(self, monkeypatch):
        """A projection swaps our line out; it never stacks a second discount on it."""
        discount_row = item('-37.50')
        registration = make_registration('250.00', [discount_row])
        monkeypatch.setattr(pricing, 'get_discount_data', lambda reg: discount_row)
        assert pricing.projected_price(registration, None, APPLIES_TO_BASE) == Decimal('250.00')
        assert pricing.projected_price(registration, PLAN_FIXED, APPLIES_TO_BASE) == Decimal('200.00')

    def test_keeps_the_price_adjustment(self, no_discount_row):
        registration = make_registration('250.00', price_adjustment='-20.00')
        assert pricing.projected_price(registration, None, APPLIES_TO_BASE) == Decimal('230.00')

    def test_floors_at_zero_like_core(self, no_discount_row):
        registration = make_registration('10.00', price_adjustment='-50.00')
        assert pricing.projected_price(registration, None, APPLIES_TO_BASE) == Decimal(0)

    def test_null_columns(self, no_discount_row):
        registration = SimpleNamespace(base_price=None, price_adjustment=None, data=[])
        assert pricing.projected_price(registration, PLAN_10, APPLIES_TO_BASE) == Decimal(0)


class TestReentrancyGuard:
    def test_flag_is_off_by_default(self):
        assert not pricing.is_pricing_in_progress()

    def test_flag_is_set_inside_the_context(self):
        with pricing.pricing_in_progress():
            assert pricing.is_pricing_in_progress()
        assert not pricing.is_pricing_in_progress()

    def test_nesting(self):
        with pricing.pricing_in_progress():
            with pricing.pricing_in_progress():
                assert pricing.is_pricing_in_progress()
            assert pricing.is_pricing_in_progress()
        assert not pricing.is_pricing_in_progress()

    def test_exception_clears_the_flag(self):
        with pytest.raises(RuntimeError):
            with pricing.pricing_in_progress():
                raise RuntimeError
        assert not pricing.is_pricing_in_progress()
