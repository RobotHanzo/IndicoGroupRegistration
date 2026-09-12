"""The reconciliation task has to run where core expects a request.

Repricing is the one thing this plugin does outside a request, and it is also
the one thing that changes what a member owes.  Every route out of that sends
`registration_state_updated` -- `pricing.sync_balance_state` when a balance
appears, `Registration.sync_state` when the price crosses zero -- and core logs
that signal in `indico/modules/events/registration/logging.py`, which reads
`session.user` with nothing guarding it.  A Celery task has no request to read
a session from unless it asks for one, so the send raises `RuntimeError`,
`reconcile.reconcile_due_groups` rolls that one group back and moves on, and
the group sits at `forming` past its deadline for ever -- while the organizer's
*Reprice now* button, which runs in a request, reprices the same group without
complaining.

Nothing about that failure is visible from the group: it is the deadline, not
the plugin, that appears not to work.  So these pin the flag, and that a
*second* task cannot be added without it.
"""

from pathlib import Path

from indico_group_registration.tasks import reconcile_groups


PACKAGE = Path(__file__).parents[1] / 'indico_group_registration'


def _task_decorators(source):
    """Every ``@celery.<something>task(...)`` decorator in a module, as text."""
    for chunk in source.split('@celery.')[1:]:
        yield chunk.partition('\ndef ')[0]


def test_reconciliation_runs_in_a_request_context():
    assert reconcile_groups.request_context is True


def test_reconciliation_runs_in_the_plugin_context():
    # The other half of the same wiring: without it `get_plugin_template_module`
    # cannot find the repricing notice's e-mail template.
    assert reconcile_groups.plugin == 'group_registration'


def test_every_task_asks_for_a_request_context():
    # Deliberately every task, not only this one.  Everything this plugin would
    # ever schedule touches a registration, and the failure is silent.
    for path in sorted(PACKAGE.rglob('*.py')):
        source = path.read_text(encoding='utf-8')
        for decorator in _task_decorators(source):
            assert 'request_context=True' in decorator, \
                f'{path.name} schedules a Celery task without a request context'
