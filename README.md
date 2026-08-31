# Indico Group Registration

Participant-run group registration with plan-based discounts for
[Indico](https://github.com/indico/indico) 3.3.

A participant picks a **group plan** while registering, gets a code and a join
link, and shares them however they like. Everyone who joins pays that plan's
rate straight away. The group **confirms itself** the moment the plan's seat
count is filled — there is no button to press. If it never fills, the plugin
reprices it at the reconciliation deadline and reports what each member still
owes.

Organizers set the plans and the disclaimer. They never have to create a group.

## How it works

### Plans

An organizer defines plans per registration form. A plan is a seat count and a
rate:

```json
[
  {"id": "p3",  "label": "Group of 3",  "size": 3,  "type": "percent", "value": 10},
  {"id": "p10", "label": "Group of 10", "size": 10, "type": "percent", "value": 15},
  {"id": "p25", "label": "Group of 25", "size": 25, "type": "amount",  "value": 50}
]
```

The seat count is both the **target and the cap**: filling it confirms the
group, and the group is then full.

### Lifecycle

| State | What it means |
| --- | --- |
| `forming` | Seats filling. Members pay the chosen plan's rate, whenever they like. |
| `confirmed` | The seat count was reached. Automatic. The rate is final and never gets worse. |
| `short` | The deadline passed with seats empty. Repriced to whatever the group does qualify for; balances may be due. |
| `dissolved` | A manager took it apart. Everyone is back on the standard rate. |

The only transition a human triggers is dissolution.

### Reconciliation, and the one thing to know before enabling this

Members may pay before their group fills. If the group then falls short, the
plugin reprices everyone — including people who have already paid.

**Indico has no concept of a partial payment or a balance.** A transaction is
one amount against one registration, and its checkout would charge the whole
new price again rather than the difference. So an already-paid member whose
price rises stays `complete` as far as Indico is concerned, and only this
plugin knows they owe anything.

The plugin gives organizers a **Balances due** list (paid amount, new price,
delta) and e-mails every affected member, but a person collects the money — at
the desk or by transfer, recorded as a manual payment. Two settings soften it:

- set the **reconciliation deadline** well before the event, so balances
  surface while there is still time to chase them;
- turn off **allow early payment** if you would rather not chase anyone.

### Sharing a group

The group panel shows the group code and the join link, each with a copy
button. **The plugin sends no invitations.** There is no invite box and no way
for a participant to make your server e-mail a stranger. Leaders share the link
themselves.

The plugin does send e-mails that nobody can trigger on demand: group
confirmed, group short with the new rate, and group dissolved.

## Installation

> **Do not let pip upgrade Indico by accident.** This plugin declares
> `indico>=3.3,<3.4`, and pip will happily *upgrade* an installed Indico to
> satisfy that. An Indico whose code is newer than its database fails on every
> page that touches a migrated table — the whole site, not just registration.
> Install into the existing Indico virtualenv and check what pip says it is
> about to do:
>
> ```bash
> /opt/indico/.venv/bin/pip install indico-plugin-group-registration
> ```
>
> If Indico appears in the "Installing collected packages" line, **run the core
> migrations before restarting** (see below). To keep pip away from Indico
> entirely:
>
> ```bash
> /opt/indico/.venv/bin/pip install --no-deps indico-plugin-group-registration
> ```

Add it to `indico.conf`:

```python
PLUGINS = {'group_registration'}
```

Then migrate. **`--all-plugins` runs core migrations too, which is what you
want** — if pip moved Indico forward, this is the step that moves the database
with it:

```bash
indico db --all-plugins upgrade
```

Sanity-check that nothing is pending:

```bash
indico db alembic current   # should match
indico db alembic heads
```

Restart Indico and its Celery workers — the reconciliation task runs every
fifteen minutes from the Celery beat schedule.

That is the whole install. The wheel ships the compiled webpack bundle, so
there is no asset build to run on the server — which is just as well, since
building one needs node and an Indico *source* checkout, and a pip-installed
Indico has neither.

### If registration pages throw `Assets for plugin group_registration have not been built`

Indico could not find `static/dist/manifest.json` next to the installed plugin,
so it refuses to render any page the plugin puts a script on — which is every
registration page in the instance, management side included.

Check what got installed:

```bash
ls /opt/indico/.venv/lib/python3.12/site-packages/indico_group_registration/static/dist/
```

If that directory is missing or empty, the plugin was installed from a source
checkout, or from a wheel built before the assets were packaged. Reinstall from
PyPI:

```bash
/opt/indico/.venv/bin/pip install --no-deps --force-reinstall indico-plugin-group-registration
```

and restart Indico. If you are deliberately running from a git checkout, build
the bundle yourself — see [Building the assets](#building-the-assets).

### If the site is throwing `UndefinedColumn` errors

Indico's code is ahead of its database. Nothing to do with this plugin's
tables; run the migrations:

```bash
indico db --all-plugins upgrade
```

Or put Indico back where it was and migrate later:

```bash
/opt/indico/.venv/bin/pip install 'indico==<your previous version>'
```

## Configuration

Event management → **Group registration**, then pick a registration form.
Nothing changes on a form until group registration is enabled for it.

| Setting | Default | Notes |
| --- | --- | --- |
| Plans | – | JSON, validated on save |
| Discount applies to | Registration fee | Or the whole price, including paid options |
| Reconciliation deadline | Registration end | Set it well before the event |
| Allow paying before the group fills | on | |
| Groups per person | 1 | 0 for no limit |
| Count registrations awaiting approval | on | Withdrawn and rejected never count |
| Reprice a confirmed group that loses a member | off | Nobody should be rebilled over somebody else's moderation |
| Disclaimer | a sensible default | Versioned; the version and time of acceptance are stored per membership |

Enabling group registration provisions two fields on the form:

- **`ext__group_plan`** — the plan picker the participant fills in. Put it
  wherever you like in the form.
- **`ext__group_discount`** — a manager-only field carrying the discount. It is
  locked, written only by the plugin, and appears as a named line on the
  invoice. Leave it alone; the plugin puts it back if it is removed.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
```

The tests here are unit tests over the money arithmetic and the code handling;
they do not need a database or Indico's pytest plugin.

### Building the assets

The plan picker is a React component compiled by Indico's own webpack setup,
which means the build needs an Indico source checkout at the version the plugin
will run against — `bin/maintenance/`, `webpack/` and `node_modules/` are not
in the Indico wheel.

```bash
git clone --branch v3.3.13 https://github.com/indico/indico ~/dev/indico
cd ~/dev/indico && npm ci && cd -

/opt/indico/.venv/bin/python build-assets.py --indico-source ~/dev/indico
```

Run it with the Python that Indico and this plugin are installed into: the
build imports the plugin to resolve its own URL rules. The result lands in
`indico_group_registration/static/dist/`, which is git-ignored and shipped as a
packaging artifact. `--dev` and `--watch` are passed straight through to
Indico's build script.

Releases do all of this in CI — see
[.github/workflows/build.yml](.github/workflows/build.yml), which builds the
bundle, packs it into the wheel and refuses to publish a wheel that is missing
its assets, templates or migrations.

## How it hooks into Indico

Nothing in core is patched. See `docs/EXTENSION-POINTS.md` for the full map
with file-and-line references, and `docs/PLAN.md` for the design. The short
version:

- registration field types via `signals.core.get_fields`
- the registration lifecycle via `registration_created`, `registration_deleted`
  and `registration_state_updated`
- `is_field_data_locked` to keep core out of the plugin's own field
- `registrant_list_items` for the Group column
- `before-render-registration-info` for the group panel
- the React field registry via `regformCustomFields`
- `get_template_customization_paths` to name the discount on the checkout page,
  using `{% extends '~...' %}` so it inherits the core template rather than
  forking it

## Licence

MIT.
