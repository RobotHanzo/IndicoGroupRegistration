"""Group plans and the arithmetic that turns one into a discount.

Nothing in here touches Indico or the database.  A plan is a seat count and a
rate; everything else in the plugin asks this module what a plan is worth and
which plan a given number of members actually qualifies for.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


#: Money is always rounded to whole cents, half up.  Indico stores prices as
#: ``numeric(11, 2)`` so anything finer would be truncated by the database
#: anyway, and we would rather do the rounding where we can test it.
CENTS = Decimal('0.01')

PERCENT = 'percent'
AMOUNT = 'amount'
DISCOUNT_TYPES = frozenset({PERCENT, AMOUNT})

#: What the discount is calculated against.
APPLIES_TO_BASE = 'base'
APPLIES_TO_TOTAL = 'total'
APPLIES_TO = frozenset({APPLIES_TO_BASE, APPLIES_TO_TOTAL})

MAX_PLANS = 20
MAX_PLAN_SIZE = 1000


class PlanError(ValueError):
    """Raised when a plan definition cannot be used."""


def _to_decimal(value, field):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PlanError(f'{field} must be a number, got {value!r}')


def quantize(value: Decimal) -> Decimal:
    """Round a money amount to whole cents."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Plan:
    """One row of the organizer's plan table."""

    id: str
    label: str
    size: int
    type: str | None = None
    value: Decimal = Decimal(0)

    @property
    def is_group(self) -> bool:
        """Whether choosing this plan means forming a group at all."""
        return self.size > 1

    @property
    def has_discount(self) -> bool:
        return self.type is not None and self.value > 0

    def serialize(self) -> dict:
        return {
            'id': self.id,
            'label': self.label,
            'size': self.size,
            'type': self.type,
            'value': float(self.value) if self.type else None,
        }


def parse_plan(raw: dict) -> Plan:
    """Build a single `Plan` from its stored representation.

    :raises PlanError: if the definition is unusable.  Callers store plans as
                       JSON, so this is the only place that gets to trust them.
    """
    if not isinstance(raw, dict):
        raise PlanError(f'plan must be an object, got {type(raw).__name__}')

    plan_id = str(raw.get('id') or '').strip()
    if not plan_id:
        raise PlanError('plan id must not be empty')

    label = str(raw.get('label') or '').strip()
    if not label:
        raise PlanError(f'plan {plan_id!r} must have a label')

    try:
        size = int(raw.get('size'))
    except (TypeError, ValueError):
        raise PlanError(f'plan {plan_id!r} has a non-integer size: {raw.get("size")!r}')
    if size < 1:
        raise PlanError(f'plan {plan_id!r} must have a size of at least 1')
    if size > MAX_PLAN_SIZE:
        raise PlanError(f'plan {plan_id!r} exceeds the maximum size of {MAX_PLAN_SIZE}')

    discount_type = raw.get('type') or None
    if discount_type is None:
        return Plan(id=plan_id, label=label, size=size)
    if discount_type not in DISCOUNT_TYPES:
        raise PlanError(f'plan {plan_id!r} has an unknown discount type: {discount_type!r}')

    value = _to_decimal(raw.get('value', 0), f'plan {plan_id!r} value')
    if value < 0:
        raise PlanError(f'plan {plan_id!r} has a negative discount')
    if discount_type == PERCENT and value > 100:
        raise PlanError(f'plan {plan_id!r} has a percentage above 100')

    return Plan(id=plan_id, label=label, size=size, type=discount_type, value=value)


def parse_plans(raw) -> tuple[Plan, ...]:
    """Build the ordered plan list, smallest seat count first.

    Ordering matters: `best_plan_for_size` walks it, and the plan picker shows
    it in this order.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise PlanError(f'plans must be a list, got {type(raw).__name__}')
    if len(raw) > MAX_PLANS:
        raise PlanError(f'at most {MAX_PLANS} plans are supported')

    plans = [parse_plan(entry) for entry in raw]

    seen_ids = set()
    seen_sizes = set()
    for plan in plans:
        if plan.id in seen_ids:
            raise PlanError(f'duplicate plan id: {plan.id!r}')
        seen_ids.add(plan.id)
        if plan.size in seen_sizes:
            # Two plans with the same seat count would make `best_plan_for_size`
            # arbitrary, and there is no sensible way to pick between them.
            raise PlanError(f'two plans share the seat count {plan.size}')
        seen_sizes.add(plan.size)

    return tuple(sorted(plans, key=lambda p: p.size))


def get_plan(plans, plan_id: str | None) -> Plan | None:
    """Look a plan up by id."""
    if not plan_id:
        return None
    return next((plan for plan in plans if plan.id == plan_id), None)


def group_plans(plans) -> tuple[Plan, ...]:
    """Only the plans that actually form a group."""
    return tuple(plan for plan in plans if plan.is_group)


def best_plan_for_size(plans, size: int) -> Plan | None:
    """The most generous group plan that `size` members actually qualify for.

    Used at reconciliation: a group that aimed for ten and reached seven falls
    back to the largest plan seven people are entitled to.  Returns ``None``
    when they qualify for no group plan at all, which means the standard rate.
    """
    candidates = [plan for plan in group_plans(plans) if plan.size <= size]
    if not candidates:
        return None
    # Largest seat count wins; on a tie (impossible after `parse_plans`, but
    # cheap to be explicit about) the bigger discount does.
    return max(candidates, key=lambda p: p.size)


def discount_for(plan: Plan | None, discountable: Decimal) -> Decimal:
    """The discount a plan is worth, as a **negative** amount in whole cents.

    `discountable` is the part of the price the discount applies to.  The
    result is clamped so it can never exceed it — a 120-euro discount on an
    80-euro fee gives back 80, not 120, because Indico would clamp the total at
    zero anyway and we would rather the invoice line matched the total.
    """
    if plan is None or not plan.has_discount:
        return Decimal(0)
    if discountable <= 0:
        return Decimal(0)

    if plan.type == PERCENT:
        raw = discountable * plan.value / Decimal(100)
    else:
        raw = plan.value

    return -min(quantize(raw), quantize(discountable))


def discountable_amount(base_price: Decimal, other_items_total: Decimal, applies_to: str) -> Decimal:
    """The part of a registration's price a discount is calculated against.

    `other_items_total` must exclude our own discount line, or applying the
    discount would change what the discount is calculated from.
    """
    if applies_to not in APPLIES_TO:
        raise PlanError(f'unknown applies_to: {applies_to!r}')
    if applies_to == APPLIES_TO_BASE:
        return max(base_price, Decimal(0))
    return max(base_price + other_items_total, Decimal(0))
