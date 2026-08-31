"""Scheduled work."""

from celery.schedules import crontab

from indico.core.celery import celery

from indico_group_registration.reconcile import reconcile_due_groups


@celery.periodic_task(name='group_registration_reconcile', run_every=crontab(minute='*/15'))
def reconcile_groups():
    """Reprice groups whose reconciliation deadline has passed.

    Runs every quarter of an hour rather than nightly: a deadline that says
    23:59 should mean it, and members are waiting to find out what they owe.
    """
    from indico_group_registration.plugin import GroupRegistrationPlugin

    reconciled = reconcile_due_groups()
    if reconciled:
        GroupRegistrationPlugin.logger.info('Reconciled %d group(s)', len(reconciled))
