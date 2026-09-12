"""Scheduled work."""

from celery.schedules import crontab

from indico.core.celery import celery

from indico_group_registration.reconcile import reconcile_due_groups


# `plugin=` is what puts the task inside this plugin's context.  Without it
# `get_plugin_template_module` cannot find the e-mail templates and the
# repricing notice blows up in the worker rather than reaching anyone.
#
# `request_context=True` because repricing changes what a member owes, and
# every route out of that sends `registration_state_updated` -- ours in
# `pricing.sync_balance_state`, core's own in `Registration.sync_state`.  Core
# logs that signal in `events/registration/logging.py:61`, which reads
# `session.user` with nothing guarding it, and a Celery task has no request to
# read a session from unless it asks for one (`core/celery/core.py:124`).  The
# `RuntimeError` lands in `reconcile.reconcile_due_groups`, which rolls that one
# group back and moves on -- so the group sits at `forming` past its deadline
# for ever while *Reprice now*, which runs in a request, works fine.
@celery.periodic_task(name='group_registration_reconcile', run_every=crontab(minute='*/15'),
                      plugin='group_registration', request_context=True)
def reconcile_groups():
    """Reprice groups whose reconciliation deadline has passed.

    Runs every quarter of an hour rather than nightly: a deadline that says
    23:59 should mean it, and members are waiting to find out what they owe.
    """
    from indico_group_registration.plugin import GroupRegistrationPlugin

    reconciled = reconcile_due_groups()
    if reconciled:
        GroupRegistrationPlugin.logger.info('Reconciled %d group(s)', len(reconciled))
