#!/usr/bin/env python
"""Build this plugin's webpack bundle into `indico_group_registration/static/dist/`.

Indico compiles plugin assets with its own webpack setup, and none of what that
needs -- `bin/maintenance/`, `webpack/`, `plugin.webpack.config.mjs`,
`node_modules/` -- ships in the Indico wheel.  So this wants an Indico *source*
checkout at the same version as the Indico the plugin runs against::

    git clone --branch v3.3.13 https://github.com/indico/indico ~/dev/indico
    (cd ~/dev/indico && npm ci)

    /opt/indico/.venv/bin/python build-assets.py --indico-source ~/dev/indico

Run it with the Python that Indico and this plugin are installed into: the
build resolves the plugin's own URL rules by importing it.

Released wheels already carry the built bundle, so this is for development and
for installs straight from a git checkout.  Extra arguments are passed through
to Indico's own `build-assets.py` -- `--dev` and `--watch` are the useful ones.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parent


def fail(message):
    sys.exit(f'error: {message}')


def check_indico_source(source: Path):
    build_assets = source / 'bin' / 'maintenance' / 'build-assets.py'
    if not build_assets.exists():
        fail(f'{source} is not an Indico source checkout (no bin/maintenance/build-assets.py).\n'
             '       Clone one with: git clone https://github.com/indico/indico')
    if not (source / 'node_modules').exists():
        fail(f'{source} has no node_modules.\n'
             f'       Install them with: cd {source} && npm ci')
    return build_assets


def check_versions(source: Path):
    """Warn if the source checkout and the installed Indico disagree.

    The bundle is compiled against the source tree but runs against the
    installed one, so a mismatch shows up as a broken page rather than a build
    error.
    """
    try:
        import indico
    except ImportError:
        fail('Indico is not importable.  Run this with the Python that Indico is installed into, '
             'e.g. /opt/indico/.venv/bin/python')

    installed = indico.__version__
    source_version = None
    for line in (source / 'indico' / '__init__.py').read_text(encoding='utf-8').splitlines():
        if line.startswith('__version__'):
            source_version = line.split('=', 1)[1].strip().strip("'\"")
            break
    if source_version and source_version != installed:
        print(f'warning: building against Indico {source_version} but Indico {installed} is installed',
              file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--indico-source', default=os.environ.get('INDICO_SOURCE'),
                        help='Path to an Indico source checkout (default: $INDICO_SOURCE)')
    args, passthrough = parser.parse_known_args()

    if args.indico_source is None:
        fail('no Indico source checkout given.  Pass --indico-source or set $INDICO_SOURCE')

    source = Path(args.indico_source).expanduser().resolve()
    build_assets = check_indico_source(source)
    check_versions(source)

    env = os.environ.copy()
    # `dump_url_map.py` builds a throwaway Flask app to resolve the plugin's own
    # endpoints; /dev/null gives it Indico's defaults, which is all it needs.
    env.setdefault('INDICO_CONFIG', os.devnull)

    cmd = [sys.executable, str(build_assets), 'plugin', *passthrough, str(PLUGIN_DIR)]
    print('running: ' + ' '.join(cmd), file=sys.stderr)
    raise SystemExit(subprocess.call(cmd, env=env))


if __name__ == '__main__':
    main()
