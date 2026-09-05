"""The management pages' template names have to survive the WP class.

`WPJinjaMixin._prefix_template` prepends `template_prefix` to whatever name a
controller passes, and `WPManageRegistration` sets that prefix to
`events/registration/` for core templates.  Inheriting from it without also
inheriting from `WPJinjaMixinPlugin` turns `group_registration:overview.html`
into `events/registration/group_registration:overview.html`, and every
management page 500s with `TemplateNotFound`.  These tests pin the two things
that stop that: no prefix, and the plugin template loader.
"""

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
