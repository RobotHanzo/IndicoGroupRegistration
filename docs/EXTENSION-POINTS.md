# Indico extension points relevant to group registration & discounts

Verified against the working copy at `D:\Web Projects\STSA\indico` — Indico `3.3.14-dev`.
Every reference below is a real, officially-supported plugin hook; none of them
requires patching core.

## 1. Pricing

`Registration.price` is computed, not stored:

```python
# indico/modules/events/registration/models/registrations.py:534-550
calc_price = Decimal(str(sum(data.price for data in self.data)))
return (base_price + price_adjustment + calc_price).max(0)
```

| Component | Written by core? | Notes |
| --- | --- | --- |
| `base_price` | yes — copied from `regform.base_price` at creation | `util.py:439` |
| field prices | yes — per `RegistrationData`, via the field impl | see below |
| `price_adjustment` | **no UI in core writes it** | only ever rendered |

Two ways for a plugin to change what someone owes:

**A. A negative `price_adjustment`.** One column, no form changes. But it
renders as a bare *"Price adjustment"* row in the invoice
(`display/_registration_summary_blocks.html:305-315`) with no label of its own,
and there is no template hook inside that macro to relabel it.

**B. A plugin-owned billable field.** Renders as a proper row — the field's
title in the Item column, `render_invoice_data()` in the Value column, and the
price in Cost (`_registration_summary_blocks.html:272-292`). This is how to get
a named discount line.

### The `calculate_price` signature matters

```python
# indico/modules/events/registration/models/form_fields.py:113
def calculate_price(self, registration_data):
    return self.field_impl.calculate_price(registration_data.data,
                                           registration_data.field_data.versioned_data)
```

The field impl receives **only the stored value and the versioned data** — not
the `RegistrationData`, and so not the `Registration`. A plugin field therefore
cannot compute a price dynamically from related rows; it must have the amount
written into its own `data` beforehand and read it back.

The *rendering* hooks do get the full object
(`fields/base.py:259-303`) and can reach anything:

| Method | Where it lands |
| --- | --- |
| `render_invoice_data(data)` | the invoice table's Value column |
| `render_summary_data(data)` | the registration summary |
| `render_email_data(data)` | registration notification e-mails |
| `render_reglist_column(data)` | the management registrant list |
| `render_spreadsheet_data(data)` | exports |

### Consumers that pick up the final price for free

- payment — `indico/modules/events/payment/plugins.py:125` (`'amount': registration.price`)
- state machine — `sync_state()` / `update_state()` use `bool(self.price)` to
  decide `unpaid` vs `complete` (`registrations.py:725`, `:760`)
- the invoice table, e-mails, receipts and exports

**Consequence:** raising the price of an *already-paid* registration does not
make Indico consider it unpaid — `is_paid` is true while the transaction is
successful, so the state stays `complete`. Core has no partial-payment or
balance concept; a shortfall has to be tracked by the plugin and collected by an
organizer.

## 2. Backend hooks

### Signals — `indico/core/signals/event/registration.py`

| Signal | Use for |
| --- | --- |
| `registration_created` (`sender=Registration`, `data`, `management`) | link the registration to a group, write its discount line |
| `registration_updated` | re-apply after modification |
| `registration_deleted` (`permanent`) | free the seat, recount |
| `registration_state_updated` (`previous_state`) | recount on withdrawal / rejection |
| `before_check_registration_email` | reject a bad or full group code before submit |
| `registrant_list_action_menu` (`sender=RegistrationForm`) | bulk actions on a group |
| `registrant_list_items` | extra *Group* column (`custom.py:25`, `CustomRegistrationListItem`) |
| `is_field_data_locked` (`sender=RegistrationFormItem`, `registration`) | lock the plugin's own discount field against participants |
| `after_registration_form_clone` | carry plans to a cloned form |

### Custom registration field types

`fields/__init__.py` resolves field types through `signals.core.get_fields`
connected via `RegistrationFormFieldBase`, so a plugin can yield its own
subclass. Base classes live in `fields/base.py`.

### RH signals — `indico/core/signals/rh.py`

`before_process` short-circuits a request handler when a receiver returns a
value; `before_check_access` can deny access. Useful for gating a core
controller, at the cost of coupling to that controller's identity.

### Template hooks

| Hook | Location |
| --- | --- |
| `regform-container-attrs` | `templates/_template_hooks.html:1` — extra `data-*` on the React regform root |
| `before-render-registration-info` | `display/_registration_summary_blocks.html:2` |
| `extra-regform-settings` / `extra-regform-edit-settings` | `management/regform.html:182`, `regform_edit.html:25` |
| `extra-registration-actions` / `before-registration-summary` | `management/_registration_details.html:28`, `:50` |
| `registration-status-flag` | `management/_reglist.html:22,46` |

Note there is **no** hook inside the `render_invoice` macro.

### Overriding a core template

`signals.plugin.get_template_customization_paths` (`core/signals/plugin.py:74`)
returns a directory that behaves exactly like `<CUSTOMIZATION_DIR>/templates`;
`setup_jinja_customization` appends it to the Jinja search path
(`web/flask/app.py:269`). A file at `<path>/core/events/payment/event_checkout.html`
replaces that core template wholesale.

Powerful, and a fork: the copy drifts on every Indico upgrade. Worth it only for
small, stable templates, and it should be pinned to a tested version range.

It is also **exclusive**. `setup_jinja_customization` appends every path the
signal yields to one search path, so the first plugin with a copy of a given
core template wins and the others are silently ignored. Two plugins that need
the same file changed cannot both use this.

### Filtering a template's context instead: `before_render_template`

Flask's own signal (`flask/templating.py`, sent from `_render`) hands receivers
the `template` and the `context` **dict** immediately before it is rendered, and
mutating that dict takes effect. Where core builds something out of the model
with no hook of its own, this reaches it without a fork — and unlike a
customization path, several plugins can each filter the same context, since each
only wraps what the last one left.

The case here is the *Customize list* dialog
(`management/reglist_filter.html:102`):

```jinja
{% for section in regform.sections if section.is_visible and section.available_fields %}
```

Every field on the form is offered as a column an organizer can switch on,
manager-only sections included, and `RegistrationFormSection.available_fields`
(`models/items.py:485`) exists for that template and nothing else — there is no
signal, no interceptable function and no `template_hook` anywhere in the file.
`reglist.hide_internal_columns` therefore swaps `regform` for a proxy whose
sections do not report the plugin's internal discount field, and the template's
own `if section.available_fields` then drops the emptied section.

The same receiver drops the field's id from `visible_items`, because
`#list-filter-select-all` (`util/list_generator.js:172`) clicks every
`.visibility:not(.enabled)` in the dialog whether it is displayed or not — so a
column merely *hidden* could still be switched on and then never switched off.

### Reusing core's e-mail dialog for a plugin's own mail

`RHRegistrationEmailRegistrants` (`controllers/management/reglists.py:327`) is
subclassable, and that is the cheapest way to let an organizer edit a mail the
plugin composes. Core already owns the rich text editor, the placeholder
description, the event locale, the sender addresses the organizer is allowed to
use, the ticket attachment and the log entry; a subclass only has to change who
the mail goes to.

Recipients come from `RHRegistrationsActionBase._process_args`
(`reglists.py:277`), which turns a posted `registration_id` list into
`self.registrations`. Overriding `_process_args` to *find* them instead — and
calling `RHManageRegFormBase._process_args` directly to skip the `use_kwargs`
one — is what makes a toolbar button one click rather than "filter the list,
select all, then compose", and it also means the posted ids are never trusted.

Two things do not come for free:

- The dialog template has to be copied, not reused.
  `management/email.html:21` builds the preview URL from the **relative**
  endpoint `.email_registrants_preview`, which resolves against whichever
  blueprint is handling the request — from a plugin blueprint that is a
  `BuildError`. Keep the `preview-email` id, though: core's delegated click
  handler is what drives the button.
- `RHRegistrationEmailRegistrantsPreview` quotes the mail against
  `getSelectedRows()[0]` in core's JS, and nothing is selected when the
  recipients were found rather than picked. A second subclass that returns the
  first recipient is less work than shipping JavaScript to fix it.

### Per-recipient figures: `signals.core.get_placeholders`

One body goes to everybody, so anything that differs per recipient can only
reach the mail as a placeholder. `replace_placeholders`
(`util/placeholders.py:201`) is called once per registration in `_send_emails`,
and the `registration-email` context is fed by
`signals.core.get_placeholders.connect_via('registration-email')`
(`registration/__init__.py:187`), which is sent **with `regform` and
`registration`** — so a receiver can decline to offer its placeholders on a form
that has nothing to do with the plugin. That is the only filter available:
placeholders are otherwise registered for the whole instance and turn up in
core's own *E-mail* action everywhere.

Two constraints follow:

- The receiver has to answer the same way for a given form every time. The
  dialog *describes* the placeholders on one call
  (`forms.py:277`) and *replaces* them on another; a name offered by the first
  and missing from the second is sent out as literal braces.
- Names are global. `named_objects_from_signal` raises on a duplicate, and it
  raises for **every** registration e-mail in the instance, not only the ones
  that use the placeholder — hence the `group_` prefix on all of ours.

### Menus, blueprints, models

- `signals.menu.items` via `'event-management-sidemenu'` for a management page.
- `IndicoPluginBlueprint` for routes.
- Plugin tables belong in a `plugin_<name>` schema; alembic revisions live in
  `<plugin_root>/migrations` (`core/plugins/__init__.py:107`).

## 3. Frontend hooks (React regform)

`indico/web/client/js/utils/plugins.jsx` provides `registerPluginObject` /
`registerPluginComponent` / `getPluginObjects` / `renderPluginComponents`.

| Entry point | Where it is consumed |
| --- | --- |
| `regformBeforeSections` / `regformAfterSections` | `form_submission/RegistrationFormSubmission.jsx:207,211` |
| `regformFormDecorators` | `RegistrationFormSubmission.jsx:198` — final-form decorators, i.e. react to field changes live |
| `regformCustomFields` | `form/fields/registry.js:269` — **names must start with `ext__`** unless `unsafeOverrideField` is set. **Register every type the server can provision, internal ones included**: `form/fields/ShowIfInput.jsx:29` reads `fieldRegistry[inputType].showIfOptions` for every item on the form with no guard, so an unregistered type throws and unmounts the whole form editor |
| `regform-{inputType}-field-item` | `form/FormItem.jsx:244` |
| `regform-{inputType}-field-settings` | `form_setup/ItemSettingsModal.jsx:160` |

Unknown `data-*` attributes on the regform root are collected into
`staticData.extraData`:

```jsx
// form_submission/index.jsx:65-68
// XXX: do NOT use extraData for anything in the core; this is solely meant as a way to
// pass data to plugins injecting custom stuff in the registration form.
```

So `regform-container-attrs` (server) → `extraData` (client) is the sanctioned
way to feed plugin data into the participant-facing form.

A field's own `view_data` is the other direction into a plugin's React
component, and `get_flat_section_submission_data`
(`registration/util.py:178`) — which assembles it for the whole form — is
`@make_interceptable`, so *another* plugin can adjust what a field is rendered
with. That is how the STSA member discount re-quotes this plugin's plan picker:
it writes `payerBasePrice` and leaves `basePrice` alone. Anything this plugin
puts in `view_data` is therefore a contract, not an implementation detail.

Plugin assets build with `indico build-assets.py plugin <dir>`, driven by a
`webpack-bundles.json` at the plugin root, and are injected with
`IndicoPlugin.inject_bundle()` (`core/plugins/__init__.py:181`).

## 4. What core does not give us

- No discount, coupon or voucher concept.
- No multi-registration checkout: `Registration.transaction_id` is a 1:1 FK, so
  one payment covers exactly one registration.
- **No partial payment and no balance.** A transaction is one amount against one
  registration; `RHPaymentCheckout` would charge the whole new price again
  rather than a difference.
- Accompanying persons (`fields/accompanying.py`) are *not* registrations — no
  own form data, no login, no individual ticket.
- `RegistrationInvitation` (`models/invitations.py`) exists, with `skip_moderation`,
  `skip_access_check` and `lock_email` — which is exactly why a participant-facing
  feature must not create one.
