"""What the management pages need from Indico that nothing else checks.

Mostly template names, which have to survive the WP class.

`WPJinjaMixin._prefix_template` prepends `template_prefix` to whatever name a
controller passes, and `WPManageRegistration` sets that prefix to
`events/registration/` for core templates.  Inheriting from it without also
inheriting from `WPJinjaMixinPlugin` turns `group_registration:overview.html`
into `events/registration/group_registration:overview.html`, and every
management page 500s with `TemplateNotFound`.  These tests pin the two things
that stop that: no prefix, and the plugin template loader -- and, at the end,
the wiring the reminder dialog's preview button cannot borrow from core.
"""

import json
import re
from pathlib import Path

import pytest
from flask_pluginengine import render_plugin_template

from indico_group_registration.controllers.management import WPGroupRegistration


TEMPLATE_NAMES = ['overview.html', 'settings.html', 'groups.html', 'balances.html']


@pytest.mark.parametrize('name', TEMPLATE_NAMES)
def test_template_name_is_not_prefixed(name):
    template = f'group_registration:{name}'
    assert WPGroupRegistration._prefix_template(template) == template


def test_renders_through_the_plugin_loader():
    assert WPGroupRegistration.render_template_func is render_plugin_template


@pytest.mark.parametrize('name', TEMPLATE_NAMES)
def test_template_exists(name):
    # `group_registration:<name>` resolves to the plugin's own `templates/`.
    templates = Path(__file__).parent.parent / 'indico_group_registration' / 'templates'
    assert (templates / name).is_file()


def test_every_email_template_exists():
    """Each name `notifications._send` is handed must be a file.

    A missing one blows up in the Celery worker with nothing reaching anyone.
    """
    package = Path(__file__).parent.parent / 'indico_group_registration'
    names = set(re.findall(r"'(group_\w+\.txt)'", (package / 'notifications.py').read_text()))
    assert names
    for name in names:
        assert (package / 'templates' / 'emails' / name).is_file(), name


def test_the_reminder_dialog_template_exists():
    """The reminder dialog does not go through `WPGroupRegistration`.

    `jsonify_template` renders it straight into the AJAX dialog, so it is not
    covered by the names above -- but a missing file still 500s the one button
    on the Groups page an organizer presses.
    """
    templates = Path(__file__).parent.parent / 'indico_group_registration' / 'templates'
    assert (templates / 'remind_forming_groups.html').is_file()


def _preview_button():
    """The `<input>` in the reminder dialog that opens the preview."""
    templates = Path(__file__).parent.parent / 'indico_group_registration' / 'templates'
    source = (templates / 'remind_forming_groups.html').read_text()
    match = re.search(r'<input[^>]*remind_forming_groups_preview[^>]*>', source, re.S)
    assert match, 'the reminder dialog has no preview button'
    return match.group(0)


def test_the_preview_button_carries_its_own_wiring():
    """It cannot borrow core's, which is why it broke the first time.

    Core binds `#preview-email` inside `setupRegistrationList()`
    (`registration/client/js/reglists.js`), and that runs from the registrant
    list template alone.  This dialog opens from the Groups page, so a button
    wearing core's id has nothing behind it and does nothing when pressed.
    `setupActionLinks` in `declarative.js` binds `data-ajax-dialog` on every
    page, which is the same handler that opens the dialog itself.
    """
    tag = _preview_button()
    assert 'data-ajax-dialog' in tag
    assert 'data-method="POST"' in tag
    assert 'preview-email' not in tag


def test_the_preview_button_sends_the_live_subject_and_body():
    """Without them the endpoint 400s on `request.form['body']`.

    jQuery only turns `data-params-selector` into an object if the attribute
    parses as JSON; anything else it keeps as a string, which
    `getParamsFromSelectors` then treats as a bare selector and sends nothing.
    """
    match = re.search(r"data-params-selector='([^']*)'", _preview_button())
    assert match, 'the preview button posts no parameters'
    assert json.loads(match.group(1)) == {'subject': '#subject', 'body': '#body'}
