"""Collecting a balance the repricing created.

Indico has one payment per registration and no notion of a difference, so a
member repriced above what they handed over keeps a *successful* transaction.
Core reads that as settled: the invoice box says "Paid", and the only button it
offers is *Mark as unpaid*, which would throw the payment away to get anywhere.
Three things have to hold for an organizer to be able to collect at all, and
each of them is somewhere core cannot see:

- the transaction the plugin writes records the **new total**, not the balance;
- the pages that show a balance carry the button that writes it;
- the "Paid" badge is corrected wherever a balance is open.
"""

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from indico.modules.events.payment.models.transactions import TransactionStatus

from indico_group_registration import pricing


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / 'indico_group_registration'
TEMPLATES = PACKAGE / 'templates'
BALANCE_ACTIONS = TEMPLATES / '_balance_actions.html'
GROUP_PANEL = TEMPLATES / '_group_panel.html'
BALANCES_PAGE = TEMPLATES / 'balances.html'
PLUGIN_PY = PACKAGE / 'plugin.py'
BADGE_JS = PACKAGE / 'client' / 'js' / 'balanceBadge.js'

#: What the server writes onto the panel and the browser reads back off it.
BADGE_MARKER = 'data-group-balance-due'

ORGANIZER = SimpleNamespace(id=7, full_name='Jasmine Lin')


def make_registration(price, paid, currency='SGD'):
    """A registration that paid `paid` against a price that is now `price`."""
    membership = SimpleNamespace(paid_amount=Decimal(paid),
                                 balance_due=max(Decimal(price) - Decimal(paid), Decimal(0)))
    return SimpleNamespace(price=Decimal(price), currency=currency, transaction='the-one-they-paid',
                           group_membership=membership)


@pytest.fixture
def payment(monkeypatch):
    """Stand in for the transaction table and the state sync.

    `record_balance_payment` is the only place in the plugin that writes a
    payment, so what it hands `PaymentTransaction` is the whole point of it.
    """
    record = SimpleNamespace(transactions=[], synced=[])

    class FakeTransaction:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            record.transactions.append(self)

    monkeypatch.setattr(pricing, 'PaymentTransaction', FakeTransaction)
    monkeypatch.setattr(pricing, 'db', SimpleNamespace(session=SimpleNamespace(flush=lambda: None)))
    monkeypatch.setattr(pricing, 'sync_balance_state', record.synced.append)
    return record


class TestRecordBalancePayment:
    def test_records_the_new_total_rather_than_the_balance(self, payment):
        """The amount is what `GroupMember.paid_amount` reads back.

        Writing the balance itself would leave the member looking as though
        they had paid 3.00 of 28.00, and the next glance at the page would show
        a larger balance than the one just collected.
        """
        registration = make_registration('28.00', '25.00')
        pricing.record_balance_payment(registration, ORGANIZER)
        assert payment.transactions[0].kwargs['amount'] == Decimal('28.00')

    def test_the_new_transaction_supersedes_the_old(self, payment):
        registration = make_registration('28.00', '25.00')
        pricing.record_balance_payment(registration, ORGANIZER)
        assert registration.transaction is payment.transactions[0]

    def test_it_is_a_successful_manual_payment(self, payment):
        """Anything else and the member still reads as owing money.

        `Registration.is_paid` and the invoice badge both come off the
        transaction's status, and `is_manual` is what picks the template the
        "Payment transaction" box renders.
        """
        written = payment.transactions
        registration = make_registration('28.00', '25.00')
        pricing.record_balance_payment(registration, ORGANIZER)
        assert written[0].kwargs['status'] == TransactionStatus.successful
        assert written[0].kwargs['provider'] == '_manual'
        assert written[0].kwargs['currency'] == 'SGD'

    def test_it_records_who_took_the_money(self, payment):
        """The keys core's own registration page reads out of `transaction.data`."""
        registration = make_registration('28.00', '25.00')
        pricing.record_balance_payment(registration, ORGANIZER)
        assert payment.transactions[0].kwargs['data'] == {'changed_by_name': 'Jasmine Lin', 'changed_by_id': 7}

    def test_it_returns_what_was_settled(self, payment):
        registration = make_registration('28.00', '25.00')
        assert pricing.record_balance_payment(registration, ORGANIZER) == Decimal('3.00')

    def test_it_takes_them_off_awaiting_payment(self, payment):
        """Core's `sync_state` leaves a priced registration where it is.

        Only `sync_balance_state` knows the balance has gone, so skipping it
        would take the money and leave the member "awaiting payment" for ever.
        """
        registration = make_registration('28.00', '25.00')
        pricing.record_balance_payment(registration, ORGANIZER)
        assert payment.synced == [registration]

    def test_nothing_owed_writes_nothing(self, payment):
        registration = make_registration('28.00', '28.00')
        assert pricing.record_balance_payment(registration, ORGANIZER) is None
        assert not payment.transactions
        assert registration.transaction == 'the-one-they-paid'

    def test_a_registration_outside_a_group_writes_nothing(self, payment):
        registration = make_registration('28.00', '25.00')
        registration.group_membership = None
        assert pricing.record_balance_payment(registration, ORGANIZER) is None
        assert not payment.transactions


class TestTheButtonThatCollectsIt:
    """The action has to be reachable, from both pages that show a balance."""

    def test_the_macro_file_exists(self):
        # `balances.html` imports it and `plugin._inject_balance_actions`
        # renders it, so a missing file 500s the balances page *and* every
        # management registration page belonging to a group member.
        assert BALANCE_ACTIONS.is_file()

    def test_the_button_posts_to_the_endpoint(self):
        source = BALANCE_ACTIONS.read_text(encoding='utf-8')
        assert 'group_registration.record_balance_payment' in source
        assert 'data-method="POST"' in source

    def test_the_endpoint_is_routed(self):
        """Read as text: importing the blueprint needs a configured Indico."""
        source = (PACKAGE / 'blueprint.py').read_text(encoding='utf-8')
        assert "'record_balance_payment', management.RHRecordBalancePayment" in source

    def test_the_balances_page_offers_it_per_row(self):
        source = BALANCES_PAGE.read_text(encoding='utf-8')
        assert "from 'group_registration:_balance_actions.html' import settle_button" in source
        assert 'settle_button(registration, member)' in source

    def test_the_management_page_offers_it_too(self):
        """Through the one hook core puts inside the registration action box."""
        source = PLUGIN_PY.read_text(encoding='utf-8')
        assert "self.template_hook('extra-registration-actions'" in source
        assert "'_balance_actions.html'" in source


class TestTheInvoiceBadge:
    def test_the_marker_is_read_where_it_is_written(self):
        """One name, two files, and nothing to fail loudly if they drift.

        The panel is the only thing that puts the attribute on the page and the
        badge script is the only thing that looks for it, so a rename on either
        side leaves the invoice box quietly saying "Paid".
        """
        assert BADGE_MARKER in GROUP_PANEL.read_text(encoding='utf-8')
        assert BADGE_MARKER in BADGE_JS.read_text(encoding='utf-8')

    def test_the_badge_script_is_started(self):
        source = (PACKAGE / 'client' / 'js' / 'index.jsx').read_text(encoding='utf-8')
        assert 'setupBalanceBadge' in source

    def test_the_bundle_reaches_the_management_page(self):
        """The organizer's copy of the invoice box tells the same lie."""
        assert re.search(r"inject_bundle\('main\.js', WPManageRegistration\)", PLUGIN_PY.read_text(encoding='utf-8'))


def test_the_panel_warns_whatever_state_the_group_is_in():
    """A balance is not only a short group's problem.

    Dissolving a group puts its members back on the standard rate, which raises
    the price of anybody who had already paid the group's -- so the warning has
    to sit after the state chain rather than inside its `short` branch, which is
    where it started out.
    """
    source = GROUP_PANEL.read_text(encoding='utf-8')
    assert source.index('You have an outstanding balance') > source.index("group.state.name == 'dissolved'")
