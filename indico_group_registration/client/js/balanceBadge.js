// Corrects the invoice box for a member who owes a repricing balance.
//
// Core prints the "Paid" badge from the transaction alone -- `render_invoice`
// in `events/registration/display/_registration_summary_blocks.html` asks only
// whether `registration.transaction.status` is successful. A member repriced
// above what they handed over still has a successful transaction, so the very
// page that shows SGD 25.00 received against a total of SGD 28.00 labels it
// Paid, on the organizer's copy and the participant's alike.
//
// That box has no template hook, and the macros in that file are shared by
// every registration page in the instance, so overriding it would be a fork
// with a blast radius far beyond this plugin. The badge is corrected here
// instead, off a marker the server puts on the group panel only when a balance
// is actually open. If core renames the badge, this finds nothing and leaves
// the page exactly as it was.

import {Translate} from 'indico/react/i18n';

function correctPaymentStatus() {
  if (!document.querySelector('[data-group-balance-due]')) {
    return;
  }
  const badge = document.querySelector('#payment-summary .payment-status.payment-done');
  if (!badge) {
    return;
  }
  badge.classList.replace('payment-done', 'payment-not-paid');
  badge.textContent = Translate.string('Balance due');
  const icon = document.createElement('i');
  icon.className = 'icon-time';
  badge.appendChild(icon);
}

export default function setupBalanceBadge() {
  correctPaymentStatus();
  // Check-in, tags and the payment buttons all re-render `#registration-details`
  // over AJAX, which puts core's own badge back. `updateHtml` announces that by
  // triggering `indico:htmlUpdated` on what it replaced, and the event bubbles.
  // `$` is Indico's own global jQuery, as in core's page scripts -- importing
  // the package would bundle a second copy with an event registry of its own,
  // which would never hear this.
  $(document).on('indico:htmlUpdated', correctPaymentStatus);
}
