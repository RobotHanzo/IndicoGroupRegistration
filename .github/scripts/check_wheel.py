"""Check that a built wheel carries everything the plugin needs at runtime.

A wheel missing its assets, templates or migrations installs cleanly and then
fails on the first request that touches them, which is a much worse way to find
out.  The asset check is the one that matters most: `static/dist/manifest.json`
is what `IndicoPlugin.inject_bundle` looks `main.js` and `main.css` up in, and
without it Indico raises `Assets for plugin group_registration have not been
built` on every registration page in the instance.
"""

import json
import sys
import zipfile
from pathlib import Path


PKG = 'indico_group_registration'

REQUIRED_FILES = [
    f'{PKG}/templates/_group_panel.html',
    f'{PKG}/templates/settings.html',
    f'{PKG}/templates/emails/group_short.txt',
    f'{PKG}/templates/emails/group_reminder.txt',
    f'{PKG}/templates/customization/core/events/payment/event_checkout.html',
    f'{PKG}/static/dist/manifest.json',
]

# Filenames here carry a date or a content hash, so match on directory and
# extension rather than on a name that changes every build.
REQUIRED_PATTERNS = [
    (f'{PKG}/migrations/', '.py'),
    (f'{PKG}/static/dist/', '.js'),
    (f'{PKG}/static/dist/', '.css'),
]

# The exact keys `plugin.py` passes to `inject_bundle`.
REQUIRED_BUNDLES = ['main.js', 'main.css']


def main(dist_dir):
    wheel = next(Path(dist_dir).glob('*.whl'), None)
    if wheel is None:
        sys.exit(f'no wheel in {dist_dir}')

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        problems = [f'missing {name}' for name in REQUIRED_FILES if name not in names]
        for prefix, suffix in REQUIRED_PATTERNS:
            if not any(n.startswith(prefix) and n.endswith(suffix) for n in names):
                problems.append(f'missing {prefix}*{suffix}')

        if f'{PKG}/static/dist/manifest.json' in names:
            manifest = json.loads(zf.read(f'{PKG}/static/dist/manifest.json'))
            problems += [f'manifest.json has no {bundle!r} entry'
                         for bundle in REQUIRED_BUNDLES if bundle not in manifest]

    if problems:
        sys.exit('{}:\n  {}'.format(wheel.name, '\n  '.join(problems)))
    print(f'{wheel.name} looks good')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'dist')
