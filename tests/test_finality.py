"""Nothing may promise a price the plugin can still take back.

A confirmed group's rate is settled only while *Reprice a confirmed group that
loses a member* is off.  With it on, `operations.recount_group` drops the group
back to `forming` the moment somebody leaves or is rejected, and the deadline
then reprices it like any other group that never filled -- so a member who was
told the amount was final reads the new one as a mistake, and the organizer
gets to explain it.

The promise is made in four places, on the way from deciding to register to
paying: the plan picker, the confirmation mail, the group panel and the
checkout.  Each has to ask the setting, and there is no test runner for the
first of them, so both sides are read as text -- what is worth catching is a
*fifth* place that promises finality and forgets.
"""

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / 'indico_group_registration'
FIELDS_PY = PACKAGE / 'fields.py'
PLAN_INPUT_JSX = PACKAGE / 'client' / 'js' / 'GroupPlanInput.jsx'

#: Wording that tells a member their amount cannot move.  Deliberately about
#: the claim rather than any one sentence: a rewrite that keeps the promise
#: keeps the obligation.
PROMISE_RE = re.compile(r'is final|then final|not change|never gets worse', re.I)

#: What asking the setting looks like on each side.  The Python name reaches
#: the templates as `RegistrationGroup.reprices_on_member_loss`; the browser
#: gets it camelized out of `GroupPlanField.view_data`.
GUARDS = {
    '.html': 'reprices_on_member_loss',
    '.txt': 'reprices_on_member_loss',
    '.jsx': 'revokeOnMemberLoss',
}


def promising_files():
    """Every participant-facing file that says an amount will not move."""
    candidates = [*(PACKAGE / 'templates').rglob('*.html'),
                  *(PACKAGE / 'templates').rglob('*.txt'),
                  *(PACKAGE / 'client' / 'js').glob('*.jsx')]
    return [path for path in candidates if PROMISE_RE.search(path.read_text(encoding='utf-8'))]


def test_the_promise_is_found_where_it_is_made():
    """Guard the scan: a rewording must not turn this suite into a no-op."""
    names = {path.name for path in promising_files()}
    assert {'group_confirmed.txt', '_group_panel.html', 'event_checkout.html',
            'GroupPlanInput.jsx'} <= names


def test_every_promise_asks_whether_it_can_be_kept():
    unguarded = [path.relative_to(ROOT).as_posix()
                 for path in promising_files()
                 if GUARDS[path.suffix] not in path.read_text(encoding='utf-8')]
    assert not unguarded, (
        f'{", ".join(sorted(unguarded))} tells a member their amount will not change without asking '
        'whether the group can lose a member and be repriced; see GroupSettings.revoke_on_member_loss'
    )


def view_data_keys():
    """The names `GroupPlanField.view_data` sends to the browser.

    Read as text because `fields.py` imports Indico, and the point of the
    comparison below is the two files agreeing rather than either one running.
    """
    source = FIELDS_PY.read_text(encoding='utf-8')
    body = source.split('def view_data(self):', 1)[1].split('\n    def ', 1)[0]
    return set(re.findall(r'^\s+(\w+)=', body, re.M))


def prop_type_names():
    """The props `GroupPlanInput` declares."""
    source = PLAN_INPUT_JSX.read_text(encoding='utf-8')
    block = re.search(r'GroupPlanInput\.propTypes = \{(.*?)\n\};', source, re.S)
    assert block, 'GroupPlanInput declares no propTypes'
    return set(re.findall(r'^\s+(\w+):', block.group(1), re.M))


def snake(name):
    return re.sub(r'([A-Z])', r'_\1', name).lower()


#: Handed to the input component by core's registration form, not by this
#: plugin's `view_data`.
CORE_PROPS = {'htmlName', 'disabled'}


def test_the_parsers_find_something():
    assert 'revoke_on_member_loss' in view_data_keys()
    assert 'revokeOnMemberLoss' in prop_type_names()


def test_every_prop_the_picker_reads_is_actually_sent():
    """A prop nothing sends is not a crash -- it is a quieter wrong answer.

    `view_data` is camelized on its way to the browser, so a name that only one
    of the two files has arrives as `undefined`: the picker then falls back to
    its default and says whatever that default says, which for a promise about
    money is the wrong sentence rendered perfectly.
    """
    missing = {name for name in prop_type_names() - CORE_PROPS if snake(name) not in view_data_keys()}
    assert not missing, (
        f'{", ".join(sorted(missing))} is read by GroupPlanInput but never put in GroupPlanField.view_data'
    )
