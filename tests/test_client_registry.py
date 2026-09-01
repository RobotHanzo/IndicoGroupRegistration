"""Every field type this plugin names has to exist in the client-side registry.

Core's form editor does not treat an unknown input type as a degraded case.  The
"show this field if" dropdown reads `fieldRegistry[inputType].showIfOptions` for
*every* item on the form, with no guard for a type nobody registered
(`form/fields/ShowIfInput.jsx`), so a field the plugin provisions and the client
does not know about does not render badly -- it throws, and takes the whole
editor React tree with it.  The manager who clicked "Configure field" gets a
blank page.

There is no JS test runner here, and `fields.py` cannot be imported without
Indico, so both sides are read as text.  The parsers are crude on purpose and
each has a test of its own; what is worth catching is a *new* field type that
nobody remembered to register.
"""

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONSTANTS_PY = ROOT / 'indico_group_registration' / 'constants.py'
INDEX_JSX = ROOT / 'indico_group_registration' / 'client' / 'js' / 'index.jsx'

#: Field type names are `ext__`-prefixed by Indico's rule and live in
#: `constants.py` by this plugin's own.
FIELD_TYPE_RE = re.compile(r"'(ext__\w+)'")

#: `registerPluginObject('group_registration', 'regformCustomFields', {name: '...'`
REGISTRATION_RE = re.compile(
    r"registerPluginObject\(\s*'group_registration',\s*'regformCustomFields',\s*\{\s*name:\s*'([^']+)'"
)


def declared_field_types():
    """The input types the Python side puts on registration forms."""
    return set(FIELD_TYPE_RE.findall(CONSTANTS_PY.read_text(encoding='utf-8')))


def registered_field_types():
    """The input types `index.jsx` hands to core's React field registry."""
    return set(REGISTRATION_RE.findall(INDEX_JSX.read_text(encoding='utf-8')))


def test_declared_field_types_are_found():
    """Guard the parser: a rename must not turn this suite into a no-op."""
    assert declared_field_types() == {'ext__group_plan', 'ext__group_discount'}


def test_registered_field_types_are_found():
    """The same for the JS side, where the formatting is a prettier run away."""
    assert 'ext__group_plan' in registered_field_types()


def test_every_field_type_is_registered_client_side():
    missing = declared_field_types() - registered_field_types()
    assert not missing, (
        f'{", ".join(sorted(missing))} goes onto registration forms but is not registered in index.jsx; '
        'core crashes the form editor on an input type its React field registry does not have'
    )
