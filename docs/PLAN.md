# Indico Group Registration — implementation plan

Plugin: `indico-plugin-group-registration` · module `indico_group_registration` ·
entry point `group_registration` · target Indico `3.3.x`, verified against
`3.3.14-dev`.

Participants — not organizers — create groups. A participant picks a **group
plan** when registering, gets a code and a link, shares them however they like,
and everyone who joins pays that plan's rate straight away. The group locks
itself the moment the plan's member count is reached. If it never fills, the
plugin reprices the group at the deadline and bills the difference.

## 1. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Who creates groups | **Any participant**, self-service, no organizer step | The requirement |
| How friends are invited | The plugin yields a **group code and a join link**. Nothing else. | The requirement — leaders share them by their own means |
| Locking | **Automatic**, the instant the plan's member count is reached | No "confirm" button to forget |
| Paying | **Any member may pay the discounted rate immediately**, before the group locks | The requirement |
| If the plan is not met | At the deadline, reprice to the **largest plan the group does qualify for**, or the standard rate if none. Members who already paid are **e-mailed a balance due**. | The requirement |
| Membership | Every member has their own registration and fills in their own data | Real tickets, badges, consent and moderation per person |
| Discount shown as | A real **"Group discount"** line item in the invoice, with the group and plan in the Value column | The requirement — see §5 |
| Consent | A **required, recorded acknowledgement** on the plan disclaimer | It is what makes "you may be denied entry" defensible |
| Default state | Group plans **off** per registration form | No behaviour change on existing events |

## 2. Plans

An organizer defines plans on the registration form. A plan is a seat count and
a rate:

```json
{
  "plans": [
    {"id": "solo", "label": "Individual",  "size": 1,  "discount": null},
    {"id": "p3",   "label": "Group of 3",  "size": 3,  "type": "percent", "value": 10},
    {"id": "p10",  "label": "Group of 10", "size": 10, "type": "percent", "value": 15},
    {"id": "p25",  "label": "Group of 25", "size": 25, "type": "amount",  "value": 50}
  ],
  "applies_to": "base"
}
```

- The plan's `size` is both the **target** and the **cap**. Reaching it locks the
  group; the group is then full and further joins are refused.
- The leader may switch to a different plan while the group is `forming`,
  **provided no member has paid yet** and the new plan's size is at least the
  current member count. Once anyone has paid, the plan is fixed — otherwise one
  person's choice silently rebills everyone else.
- `applies_to`: `base` (the registration fee only, so paid add-ons like the
  dinner are never discounted) or `total`. Default `base` — it is the one
  organizers can explain when a participant queries an invoice.
- `evaluate(plan, registration) -> Decimal` is a pure function, unit-tested with
  no Indico objects in sight.

## 3. Lifecycle

```
forming ──(member count reaches plan size)──▶ confirmed
   │
   └──(reconciliation deadline passes)──────▶ short
                                               (repriced, balances due)

any state ──(manager acts)──▶ dissolved   (all discounts reverted)
```

- **`forming`** — seats filling. Every member is priced at the chosen plan's
  rate and **may pay it right now**. There is no checkout gate.
- **`confirmed`** — reached automatically, never by a button. The plan is met,
  the rate is final, and it never gets worse afterwards: if a member is later
  rejected or withdraws, the remaining members keep the confirmed rate
  (`revoke_on_member_loss`, default off — nobody should be rebilled because
  somebody else's moderation failed). Turned on, the group drops back to
  `forming` rather than being repriced on the spot — it keeps its plan's rate
  and loses only its exemption from §4 — so the four places that quote a
  confirmed member's price (plan picker, confirmation mail, group panel,
  checkout) all ask `RegistrationGroup.reprices_on_member_loss` before calling
  the amount final. A promise made on one page and taken back on another is the
  one thing worse than the repricing itself.
- **`short`** — the deadline passed with the plan unmet. See §4.
- **`dissolved`** — manager action; every member reverts to the standard rate.

Which registration states count toward the target is a setting
(`count_pending`), defaulting to everything except rejected and withdrawn.

## 4. Reconciliation — the part that has to be right

A Celery task runs at `reconciliation_dt` (default: the registration deadline)
over every group still `forming`.

1. Count qualifying members → `N`.
2. Pick the **largest plan whose `size <= N`**. If none qualifies, the standard
   rate applies.
3. Set that as the group's `effective_plan`, move the group to `short`, and
   rewrite every member's discount line.
4. For each member, `balance_due = registration.price − amount already paid`:
   - **Unpaid members** simply see the higher price and pay it at checkout as
     normal. Nothing special happens.
   - **Members who already paid** keep their successful transaction, so core
     leaves them `complete` and Indico does not consider them to owe anything.
     The plugin records the balance, shows it on their registration page, and
     **e-mails them the new payable amount**.
5. Every member is e-mailed either way: what the group reached, what plan now
   applies, what they now owe.

### The honest limitation

Indico has no concept of a partial payment or a balance: a transaction is
one amount against one registration, and `RHPaymentCheckout` would charge the
*whole* new price again rather than the difference. So collecting a shortfall is
**an organizer action, not a self-service one**. The plugin gives them the tools
— a *Balances due* list per form showing paid amount, new price and delta, with
links into Indico's manual transaction entry — but somebody collects it at the
desk or by transfer.

This is the direct cost of letting people pay before the group is complete, and
it is worth stating plainly up front. Two mitigations, both settings:

- put `reconciliation_dt` comfortably before the event, so balances surface
  while there is still time to chase them;
- `allow_early_payment` per form, so an organizer who does not want to chase
  anybody can require the group to confirm first.

Members who never settle are the case the disclaimer exists for: the organizer
rejects or withdraws them from the *Balances due* list.

## 5. Showing the discount properly

The discount is **not** a `price_adjustment`. It is a plugin-owned billable
field, `ext__group_discount`, auto-provisioned on the form when group plans are
enabled, so it renders as a real row in the invoice table:

| Item | Value | Cost |
| --- | --- | --- |
| Registration fee | | €250.00 |
| **Group discount** | Acme Corp · 8 of 10 members · 15% off | **−€37.50** |
| | | **Total €212.50** |

Mechanically:

- `RegistrationFormField.calculate_price()` passes the field impl only the
  stored value and the versioned data (`models/form_fields.py:113`) — *not* the
  registration — so the plugin writes the computed amount into the field's data
  (`{"group": "ACME7QK2", "plan": "p10", "rate": "15", "amount": "-37.50"}` --
  the amount is stored as a string so no float ever rounds it) and
  `calculate_price` reads it back.
- `render_invoice_data(data)` *does* receive the whole `RegistrationData`
  (`fields/base.py:264`), so it renders the group, the plan and the progress in
  the Value column. `render_summary_data`, `render_email_data` and
  `render_reglist_column` get the same treatment.
- `Registration.price` picks the row up through
  `sum(data.price for data in self.data)`, so payment, the state machine,
  e-mails, receipts and exports are all correct with no core changes.
- The field is manager-only and locked to participants via
  `is_field_data_locked`; the plugin re-provisions it if an organizer deletes it.

`price_adjustment` is deliberately left alone, so organizers keep it for genuine
one-off adjustments.

The checkout page itself (`event_checkout.html`) only prints a single total and
has no hook, so the plugin registers a template customization path
(`signals.plugin.get_template_customization_paths`).

**This turned out not to be a fork.** Indico's `CustomizationLoader` resolves a
leading `~` to the *original* template (`web/flask/templating.py:252`), so the
override is six lines:

```jinja
{% extends '~events/payment/event_checkout.html' %}
{% block content %}
    {# name the group discount #}
    {{ super() }}
{% endblock %}
```

It inherits everything core does, including future changes to the page. The
earlier plan budgeted for a forked copy that would drift on upgrades; that cost
is gone.

## 6. Disclaimer and consent

Choosing any plan with `size > 1` requires a ticked acknowledgement before the
form will submit:

> This is a group rate. It applies only if **10 people** join group *Acme Corp*
> by **31 October**. If the group is short, you will be charged the rate your
> group does qualify for — up to the standard **€250.00** — and you will be
> asked to pay the difference even if you have already paid. Registrations with
> an unpaid balance may be cancelled, and entry to the event may be refused.

- The exact wording is an organizer setting with a `disclaimer_version`; the
  version and timestamp are stored on the membership row, so there is a record
  of what each person actually agreed to.
- The same warning is repeated on the confirmation page, in the group panel
  while `forming`, above the invoice box, and in the registration e-mail.
- The figures in it are rendered live from the chosen plan — never hardcoded.

## 7. Sharing a group

The group panel on the leader's registration page shows exactly two things worth
copying: the **group code** and the **join link**, each with a copy button, plus
the member list and the seats remaining.

The plugin sends **no invitations**. There is no invite box, no address list, no
throttle to tune, and no way for a participant to make your server e-mail a
stranger. Leaders share the link themselves.

The plugin still sends system e-mails that are not participant-triggered: group
confirmed, group short with the new amount, and balance due. An *organizer* can
also send one by hand: a reminder to every member of every group still forming,
quoting the deadline and what each member would owe if the group were repriced
at its current size. It goes only to a group's own members, so the rule holds --
nothing a participant does makes the server mail anyone.

That last one is the only e-mail whose wording is not the plugin's. It opens in
core's own e-mail dialog with a draft and the recipients already found, and the
per-member figures reach it as `{group_*}` placeholders Indico replaces per
recipient — so an organizer can rewrite the text without losing the numbers,
and without the plugin having to own a rich text editor, a preview, a sender
list or a placeholder engine of its own.

## 8. Data model — schema `plugin_group_registration`

**`group_settings`** — one row per registration form
`registration_form_id` PK/FK · `enabled` · `plans` JSONB · `applies_to` ·
`reconciliation_dt` · `allow_early_payment` · `max_groups_per_user` ·
`count_pending` · `revoke_on_member_loss` · `disclaimer_text` ·
`disclaimer_version`

**`groups`**
`id` · `registration_form_id` FK · `code` unique per form · `name` ·
`leader_registration_id` FK · `plan_id` · `target_size` · `effective_plan_id` ·
`state` (`forming`/`confirmed`/`short`/`dissolved`) · `join_uuid` ·
`created_dt` · `confirmed_dt` · `reconciled_dt`

**`group_members`**
`id` · `group_id` FK · `registration_id` FK **unique** (one group per
registration) · `joined_dt` · `applied_amount` · `disclaimer_version` ·
`disclaimer_accepted_dt`

There is no invitations table.

Alembic revisions live in `indico_group_registration/migrations/`
(`indico/core/plugins/__init__.py:107`).

## 9. Concurrency

Every join, leave, plan change and lock takes `SELECT … FOR UPDATE` on the group
row first. Without it two people submitting simultaneously both read the
pre-join count, and a 10-seat group either overfills or never trips its own
auto-lock. The lock check and the state transition happen in the same
transaction as the membership insert.

## 10. Security and abuse

Dropping leader-triggered e-mail removes the worst of it. What remains:

- **No core `RegistrationInvitation`.** A participant able to create one could
  set `skip_access_check` or `skip_moderation` and hand out entry to a protected
  event. Joining is a plain registration through the form's own access check.
- **Join links are capability URLs.** Random UUID, revocable by regenerating,
  and joining still passes every normal check.
- **Rate-limit the code-validation endpoint** — it is otherwise an oracle for
  guessing group codes. Codes are eight characters from an unambiguous alphabet
  and the endpoint returns nothing beyond the group name, seats and plan.
- **Fake members to reach a plan size.** Core enforces one registration per
  e-mail per form, fakes are visible to organizers and consume the participant
  cap, and — unlike a floating tier — a fake member must actually *pay* or it
  surfaces on the balances list. Managers can dissolve any group in one click.
- **The group name is user input** rendered in e-mails and management pages.
  Escape it, cap its length.
- **`max_groups_per_user`** stops one person opening groups in bulk.

## 11. Code layout

```
indico_group_registration/
  __init__.py            # version, bound gettext
  plugin.py              # signals, template hooks, bundle injection
  models/
    groups.py  members.py  settings.py
  migrations/            # alembic revisions
  plans.py               # pure plan evaluator + reconciliation choice
  fields.py              # ext__group_discount field type
  operations.py          # join / leave / switch plan / lock / dissolve, row-locked
  pricing.py             # writes the discount line on every member
  reconcile.py           # deadline repricing + balance calculation
  controllers/
    display.py           # participant: create, join, leave, group panel
    management.py        # organizer: plans, group list, balances due, dissolve
  forms.py  schemas.py  notifications.py  tasks.py  blueprint.py
  templates/             # group panel, management pages, e-mails
  client/js/             # React: plan picker + disclaimer, group panel
webpack-bundles.json     # built with `indico build-assets.py plugin <dir>`
```

Packaging, ruff config and CI mirror `indico-plugin-sentry`.

## 12. Milestones

1. **Skeleton** — package, plugin class, `plugin_group_registration` schema,
   first migration, CI.
2. **Plan evaluator** — `plans.py`: pricing and the "largest plan that fits"
   reconciliation choice, with exhaustive unit tests. Needs no Indico.
3. **Discount line item** — the `ext__group_discount` field type, its invoice,
   summary, e-mail and reglist rendering, and auto-provisioning.
4. **Models and operations** — join, leave, switch plan, auto-lock and dissolve
   under row locks; `apply_group_pricing()`.
5. **Organizer settings** — plan editor, disclaimer text and version, a group
   list with dissolve, and a *Group* column in the registrant list.
6. **Participant frontend** — the plan picker with its live disclaimer, code
   validation, and the group panel with code and join link.
7. **Reconciliation** — the deadline task, balance calculation, the *Balances
   due* management view, and all three e-mails.
8. **Hardening** — concurrency tests for simultaneous joins at the boundary,
   form-clone support, i18n, README, an end-to-end pass on a real instance.

Milestones 2–4 carry the money logic and no UI; they get the heaviest tests.

## 13. What to verify on a live instance

- **Auto-lock at the boundary.** Two people submitting into the ninth and tenth
  seat at once must produce exactly one confirmed group of ten, not eleven
  members and not a group stuck at `forming`.
- **A paid member being repriced.** Confirm core leaves them `complete`, that
  `registration.price` reflects the new rate, and that the balance we compute
  matches the transaction.
- **Modification after confirmation.** A member editing a billable field re-runs
  `modify_registration`; confirm our discount line survives it.
- **Field survival.** An organizer deleting or reordering the discount field
  must not corrupt pricing — re-provision and log.
- **Moderation interplay.** A member rejected after the group confirmed must not
  silently rebill the others while `revoke_on_member_loss` is off.
- **Currency.** Every member sits on one form, so there is one currency and no
  cross-currency case. Assert it anyway.
