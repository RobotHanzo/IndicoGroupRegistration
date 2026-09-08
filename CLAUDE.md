# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An **Indico 3.3 plugin** (`indico-plugin-group-registration`, module
`indico_group_registration`, entry point `group_registration`) that lets
participants — not organizers — form registration groups and earn a plan-based
discount. It patches nothing in core: every hook is an officially supported
Indico extension point, mapped with file-and-line references in
[docs/EXTENSION-POINTS.md](docs/EXTENSION-POINTS.md). [docs/PLAN.md](docs/PLAN.md)
records the design decisions and why each was made; [README.md](README.md) is
the operator-facing manual.

## Commands

```bash
pip install -e '.[dev]'        # into the Indico virtualenv — the plugin imports indico.*
pytest                          # unit tests only; no database, no Indico pytest plugin
pytest tests/test_pricing.py::TestComputeDiscount::test_fixed_amount   # a single test
ruff check .
```

Tests import Indico, so they need it installed (Python 3.12; `requires-python`
is pinned `>=3.12.2, <3.13`). `pyproject.toml` sets `addopts = '-p no:indico'`
because Indico's own pytest plugin drags in its whole database fixture stack and
needs docker. CI additionally exports `INDICO_CONFIG=/dev/null` (`NUL` on
Windows) so Indico reads its defaults.

The tests cover the pure modules — money arithmetic, plan parsing, code
handling, the settings form, the WP template-name trap. Nothing that talks to
the database is exercised, so CI has a separate step that simply imports every
module in the package; keep that honest by never letting import-time code need a
request or a session.

### Assets

The plan picker is React compiled by **Indico's own webpack setup**, which needs
an Indico *source* checkout (`bin/maintenance/`, `webpack/`, `node_modules/` are
not in the Indico wheel):

```bash
git clone --branch v3.3.13 https://github.com/indico/indico ~/dev/indico && (cd ~/dev/indico && npm ci)
/opt/indico/.venv/bin/python build-assets.py --indico-source ~/dev/indico   # --dev / --watch pass through
```

Output lands in `indico_group_registration/static/dist/` (git-ignored, shipped
as a packaging artifact via `[tool.hatch.build] artifacts`). Without
`static/dist/manifest.json` next to the installed plugin, Indico raises *Assets
for plugin group_registration have not been built* on **every registration page
in the instance**, so `.github/scripts/check_wheel.py` blocks any release whose
wheel is missing it, or its templates or migrations.

### Release

`.github/workflows/publish.yml` fires on a GitHub release, checks the tag
matches `__version__` in `indico_group_registration/__init__.py`, calls the
reusable `build.yml`, and publishes to PyPI by trusted publishing. Bump
`__version__` and tag `v<version>`.

## Architecture

Layered so the lower half never imports the upper half:

| Module | Role |
| --- | --- |
| `plans.py` | Pure. A `Plan` is a seat count and a rate; parsing, validation, `best_plan_for_size`, `discount_for`. Touches neither Indico nor the DB. |
| `models/` | `RegistrationGroup`, `GroupMember`, `GroupSettings` (per registration form). |
| `pricing.py` | Writes the discount onto registrations and re-syncs their state. |
| `operations.py` | Every mutation of a group, always under a row lock. |
| `reconcile.py` / `tasks.py` | Repricing groups that never filled; the Celery beat task. |
| `reminders.py` | Pure. The wording the organizer's reminder dialog opens with. |
| `placeholders.py` | The `{group_*}` e-mail placeholders those figures arrive as. |
| `handlers.py` | What to do when Indico announces a lifecycle event. |
| `fields.py` | The two registration field types. |
| `util.py` | Lookups plus field provisioning. |
| `controllers/`, `forms.py`, `blueprint.py`, `plugin.py` | Web layer and the single place every hook is registered. |

### Invariants worth knowing before editing

- **Money flows one way through `plans.py`.** Nothing else computes a discount.
  Amounts are `Decimal`, quantized to cents half-up, and the discount is stored
  as a **negative** number clamped to the discountable amount.
- **Group state is only ever changed under `operations.lock_group`.** Two people
  submitting into the last two seats otherwise both read the same count, and the
  group either overfills or never trips its own auto-confirm. `count_qualifying`
  counts from the database, not from the session.
- **A group's price comes from its `pricing_plan`, not from how full it is.**
  That is what makes early payment safe: nothing moves until reconciliation or
  dissolution.
- **Nothing may call a confirmed rate final without asking
  `revoke_on_member_loss`.** With that setting on, `operations.recount_group`
  drops a group that loses a member back to `forming` — it keeps its plan's
  rate, but the deadline can reprice it again. Four places quote a confirmed
  member's price: the plan picker, `group_confirmed.txt`, the group panel and
  the forked checkout. Each asks `RegistrationGroup.reprices_on_member_loss`
  (`revokeOnMemberLoss`, out of `GroupPlanField.view_data`, in the picker), and
  `tests/test_finality.py` fails any file that makes the promise without the
  guard.
- **`target_size` and `plan_id` are copied onto the group at creation**, and plan
  ids are opaque generated keys (`forms._new_plan_id`). Renaming, repricing or
  reordering a plan must never retarget a group already forming under it.
- **The plan picker is given two fees, and only one of them is this plugin's.**
  `basePrice` is the form's standard fee and `payerBasePrice` is what the person
  filling the form in pays before a group plan; a plugin that discounts the same
  registration (the STSA member discount) overwrites the second. `planPrice` in
  `GroupPlanInput.jsx` works the plan's rate out from whichever the `applies_to`
  setting names — the same choice `pricing.compute_discount` makes on the server
  — so pricing a percentage plan off the discounted fee when `applies_to` is
  `base` would quietly compound two discounts the server keeps separate.
- **`calculate_price` cannot see the registration.** Core hands the field impl
  only the stored value and versioned data, so the amount has to be *written
  into* `ext__group_discount`'s value beforehand and read back. The `render_*`
  methods do get the full `RegistrationData` and can reach the group.
- **Indico has no partial payment or balance.** A repriced-upward member whose
  transaction is still successful reads as settled everywhere, so
  `pricing.sync_balance_state` moves them back to `unpaid` for exactly as long as
  a balance is due (and only between `complete` and `unpaid` — pending, rejected
  and withdrawn are somebody else's decision). Anything that changes what a
  member owes must go through it.
- **`pricing.pricing_in_progress()` is a thread-local re-entrancy guard.** Our
  own `sync_state` / `sync_balance_state` calls re-fire
  `registration_state_updated`; `handlers.handle_registration_state_updated`
  returns early while it is active. Do not remove it to "simplify" the handler.
- **Both fields are locked via `is_field_data_locked`**, which is what makes core
  skip them — so `util.set_discount_data` has to create the `RegistrationData`
  row itself, and `leave_group` has to call `clear_plan_choice` (a stale answer
  would silently re-join the person on their next edit).
- **The discount field lives in a manager-only section**, provisioned on demand
  by `util.provision_*` and put back if an organizer deletes it. Create fields
  with `parent=<section>` — appending to `section.children` autoflushes a
  half-built field and trips a check constraint.
- **Every model must live in the `plugin_group_registration` schema**
  (`models/__init__.SCHEMA`); Indico refuses to load a plugin that adds a table
  outside its own `plugin_*` schema. Alembic revisions go in `migrations/`.
- **Celery only sees imported modules.** `plugin.py` connects
  `signals.core.import_tasks` purely to import `tasks`; without it the
  reconciliation task is never registered.
- **`WPGroupRegistration` lists `WPJinjaMixinPlugin` first**, before
  `WPManageRegistration` — otherwise the inherited `template_prefix` mangles
  `group_registration:overview.html` and every management page 500s.
  `tests/test_views.py` pins this.
- **Custom regform field names must start with `ext__`**; core's frontend
  registry rejects anything else. The names live in `constants.py` and are
  repeated in `client/js/index.jsx`.
- **The internal discount field is hidden in three separate places**, because
  core exposes every field on a form in three: the form editor (the React
  component draws only the marker `client/styles/main.scss` hides its section
  by), the participant's form (the section is manager-only), and the registrant
  list's *Customize list* dialog (`reglist.hide_internal_columns`). Anything
  that adds a fourth internal field has to do all three.
- The plugin **never sends an invitation or any e-mail a participant can
  trigger**. Group-state changes mail: confirmed, short, dissolved. The one
  hand-sent mail is the organizer's reminder to forming groups
  (`RHRemindFormingGroups`), and it reaches only a group's own members. Keep it
  that way — a participant-facing endpoint that mails a stranger is the thing
  this design exists to avoid.
- **The reminder's wording is the organizer's, so its figures are placeholders.**
  `RHRemindFormingGroups` subclasses core's `RHRegistrationEmailRegistrants` and
  changes only who it goes to; the text comes from `reminders.default_body` and
  every per-member number in it is a `{group_*}` placeholder core replaces per
  recipient. The dialog template is a *copy* of core's `management/email.html`
  because that file builds the preview URL from a relative endpoint, which is a
  `BuildError` from a plugin blueprint. Recipients are found, never read off the
  request — `_ReminderMixin` skips the `use_kwargs` `_process_args` that would
  trust a posted id list.
- **The preview button cannot be core's.** Core binds `#preview-email` inside
  `setupRegistrationList()`, which runs from the registrant list template alone
  — on the Groups page that handler does not exist, so borrowing the id gives a
  button that does nothing. It is declarative instead (`data-ajax-dialog` plus
  `data-params-selector`, both bound on every page), and the object form of
  `data-params-selector` is why `pyproject.toml` floors Indico at **3.3.5**
  rather than 3.3. `tests/test_views.py` pins the attributes.
- **`plugin._get_email_placeholders` must answer the same way for a given form
  every time.** The dialog *describes* the placeholders on one call and
  *replaces* them on another, so a name offered by the first and missing from
  the second goes out as literal braces. It gates on `is_enabled(regform)` and
  nothing else, and every name is prefixed `group_`: `named_objects_from_signal`
  raises on a duplicate for *every* registration e-mail in the instance, not just
  the ones using the placeholder. `tests/test_reminders.py` pins both the prefix
  and that the default wording only uses names something registers.
- **The deadline is resolved in two places that must agree.**
  `reconcile.effective_deadline_column` (SQL, what the Celery task selects on)
  and `GroupSettings.get_reconciliation_dt` (Python, what pages and the reminder
  quote) both fall back reconciliation date → registration close → event start.
  `tests/test_deadline.py` pins the Python side; change both or neither.

### Only one core template is forked

`templates/customization/core/events/payment/event_checkout.html`, served via
`get_template_customization_paths`, and it `{% extends '~...' %}` so it inherits
rather than copies. Do not add more: a fork drifts on every Indico upgrade, and
a customization path replaces a core template *wholesale* — two plugins wanting
the same file is a silent contest one of them loses.

That last point is why `reglist.py` filters the *Customize list* dialog through
Flask's own `before_render_template` rather than forking
`management/reglist_filter.html`: the STSA plugin has the same internal field
and the same need, and receivers compose where template overrides do not.

## Style

`ruff.toml` deliberately mirrors Indico core's own config so the plugin reads
like the codebase it extends: single quotes, 120 columns, **isort is off on
purpose** (ruff cannot reproduce Indico's grid-style import wrapping — match
core by hand). Comments in this codebase explain *why*, usually a trap in core;
follow that register rather than restating what the code does.
