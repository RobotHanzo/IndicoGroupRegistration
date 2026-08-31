"""Scheduled work."""

from celery.schedules import crontab

from indico.core.celery import celery

from indico_group_registration.reconcile import reconcile_due_groups


# `plugin=` is what puts the task inside this plugin's context.  Without it
# `get_plugin_template_module` cannot find the e-mail templates and the
# repricing notice blows up in the worker rather than reaching anyone.
@celery.periodic_task(name='group_registration_reconcile', run_every=crontab(minute='*/15'),
                      plugin='group_registration')
def reconcile_groups():
    """Reprice groups whose reconciliation deadline has passed.

    Runs every quarter of an hour rather than nightly: a deadline that says
    23:59 should mean it, and members are waiting to find out what they owe.
    """
    from indico_group_registration.plugin import GroupRegistrationPlugin

    reconciled = reconcile_due_groups()
    if reconciled:
        GroupRegistrationPlugin.logger.info('Reconciled %d group(s)', len(reconciled))
