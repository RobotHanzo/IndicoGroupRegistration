from decimal import Decimal

import pytest

from indico_group_registration.plans import (
    APPLIES_TO_BASE,
    APPLIES_TO_TOTAL,
    Plan,
    PlanError,
    best_plan_for_size,
    discount_for,
    discountable_amount,
    get_plan,
    group_plans,
    parse_plan,
    parse_plans,
    quantize,
)


RAW_PLANS = [
    {'id': 'solo', 'label': 'Individual', 'size': 1},
    {'id': 'p10', 'label': 'Group of 10', 'size': 10, 'type': 'percent', 'value': 15},
    {'id': 'p3', 'label': 'Group of 3', 'size': 3, 'type': 'percent', 'value': 10},
    {'id': 'p25', 'label': 'Group of 25', 'size': 25, 'type': 'amount', 'value': 50},
]


@pytest.fixture
def plans():
    return parse_plans(RAW_PLANS)


class TestParsing:
    def test_sorted_by_size(self, plans):
        assert [p.id for p in plans] == ['solo', 'p3', 'p10', 'p25']

    def test_values_are_decimal(self, plans):
        assert get_plan(plans, 'p10').value == Decimal('15')
        assert isinstance(get_plan(plans, 'p10').value, Decimal)

    def test_plan_without_discount(self, plans):
        solo = get_plan(plans, 'solo')
        assert solo.type is None
        assert not solo.has_discount
        assert not solo.is_group

    def test_empty(self):
        assert parse_plans(None) == ()
        assert parse_plans([]) == ()

    @pytest.mark.parametrize(('raw', 'message'), (
        ({'label': 'x', 'size': 2}, 'id must not be empty'),
        ({'id': ' ', 'label': 'x', 'size': 2}, 'id must not be empty'),
        ({'id': 'a', 'size': 2}, 'must have a label'),
        ({'id': 'a', 'label': 'x', 'size': 'many'}, 'non-integer size'),
        ({'id': 'a', 'label': 'x', 'size': 0}, 'size of at least 1'),
        ({'id': 'a', 'label': 'x', 'size': 5000}, 'maximum size'),
        ({'id': 'a', 'label': 'x', 'size': 2, 'type': 'freebie'}, 'unknown discount type'),
        ({'id': 'a', 'label': 'x', 'size': 2, 'type': 'percent', 'value': -1}, 'negative discount'),
        ({'id': 'a', 'label': 'x', 'size': 2, 'type': 'percent', 'value': 101}, 'above 100'),
        ({'id': 'a', 'label': 'x', 'size': 2, 'type': 'amount', 'value': 'free'}, 'must be a number'),
    ))
    def test_rejects_bad_plan(self, raw, message):
        with pytest.raises(PlanError, match=message):
            parse_plan(raw)

    def test_rejects_duplicate_id(self):
        with pytest.raises(PlanError, match='duplicate plan id'):
            parse_plans([
                {'id': 'a', 'label': 'x', 'size': 2},
                {'id': 'a', 'label': 'y', 'size': 3},
            ])

    def test_rejects_duplicate_size(self):
        with pytest.raises(PlanError, match='share the seat count'):
            parse_plans([
                {'id': 'a', 'label': 'x', 'size': 3, 'type': 'percent', 'value': 10},
                {'id': 'b', 'label': 'y', 'size': 3, 'type': 'percent', 'value': 20},
            ])

    def test_rejects_non_list(self):
        with pytest.raises(PlanError, match='must be a list'):
            parse_plans({'id': 'a'})

    def test_rejects_too_many(self):
        raw = [{'id': f'p{n}', 'label': f'p{n}', 'size': n + 1} for n in range(21)]
        with pytest.raises(PlanError, match='at most 20 plans'):
            parse_plans(raw)

    def test_serialize_roundtrips(self, plans):
        assert parse_plans([p.serialize() for p in plans]) == plans


class TestBestPlanForSize:
    def test_exact_match(self, plans):
        assert best_plan_for_size(plans, 10).id == 'p10'

    def test_falls_back_to_the_largest_that_fits(self, plans):
        # The case the reconciliation task exists for: aimed at 10, reached 7.
        assert best_plan_for_size(plans, 7).id == 'p3'

    def test_overshoot_takes_the_best_qualifying(self, plans):
        assert best_plan_for_size(plans, 24).id == 'p10'

    @pytest.mark.parametrize('size', (0, 1, 2))
    def test_no_qualifying_plan(self, plans, size):
        assert best_plan_for_size(plans, size) is None

    def test_ignores_the_individual_plan(self, plans):
        # `solo` has size 1 and would otherwise "fit" every group.
        assert best_plan_for_size(plans, 2) is None

    def test_no_plans_at_all(self):
        assert best_plan_for_size((), 50) is None

    def test_group_plans_excludes_solo(self, plans):
        assert [p.id for p in group_plans(plans)] == ['p3', 'p10', 'p25']


class TestDiscountFor:
    def test_percent(self, plans):
        assert discount_for(get_plan(plans, 'p10'), Decimal('250.00')) == Decimal('-37.50')

    def test_amount(self, plans):
        assert discount_for(get_plan(plans, 'p25'), Decimal('250.00')) == Decimal('-50.00')

    def test_rounds_half_up_to_cents(self, plans):
        # 10% of 33.33 is 3.333, and 15% of 0.10 is 0.015 -> 0.02
        assert discount_for(get_plan(plans, 'p3'), Decimal('33.33')) == Decimal('-3.33')
        assert discount_for(get_plan(plans, 'p10'), Decimal('0.10')) == Decimal('-0.02')

    def test_never_exceeds_the_discountable_amount(self, plans):
        # A 50-euro fixed discount on a 20-euro fee gives back 20, not 50 --
        # otherwise the invoice line would not match the clamped total.
        assert discount_for(get_plan(plans, 'p25'), Decimal('20.00')) == Decimal('-20.00')

    def test_full_percent(self):
        plan = Plan(id='free', label='Free', size=2, type='percent', value=Decimal(100))
        assert discount_for(plan, Decimal('250.00')) == Decimal('-250.00')

    def test_no_plan(self):
        assert discount_for(None, Decimal('250.00')) == Decimal(0)

    def test_plan_without_discount(self, plans):
        assert discount_for(get_plan(plans, 'solo'), Decimal('250.00')) == Decimal(0)

    @pytest.mark.parametrize('discountable', (Decimal(0), Decimal('-10.00')))
    def test_nothing_to_discount(self, plans, discountable):
        assert discount_for(get_plan(plans, 'p10'), discountable) == Decimal(0)

    def test_result_is_always_negative_or_zero(self, plans):
        for plan in plans:
            assert discount_for(plan, Decimal('250.00')) <= 0


class TestDiscountableAmount:
    def test_base_only_ignores_add_ons(self):
        amount = discountable_amount(Decimal('250.00'), Decimal('80.00'), APPLIES_TO_BASE)
        assert amount == Decimal('250.00')

    def test_total_includes_add_ons(self):
        amount = discountable_amount(Decimal('250.00'), Decimal('80.00'), APPLIES_TO_TOTAL)
        assert amount == Decimal('330.00')

    def test_never_negative(self):
        amount = discountable_amount(Decimal('250.00'), Decimal('-400.00'), APPLIES_TO_TOTAL)
        assert amount == Decimal(0)

    def test_rejects_unknown_mode(self):
        with pytest.raises(PlanError, match='unknown applies_to'):
            discountable_amount(Decimal('250.00'), Decimal(0), 'everything')


def test_quantize():
    assert quantize(Decimal('1.005')) == Decimal('1.01')
    assert quantize(Decimal('1.004')) == Decimal('1.00')
