from indico.core.plugins import IndicoPluginBlueprint

from indico_group_registration.controllers import display, management


blueprint = IndicoPluginBlueprint('group_registration', __name__, url_prefix='/event/<int:event_id>')

# -- participant -------------------------------------------------------------

blueprint.add_url_rule('/registrations/<int:reg_form_id>/group/join/<join_uuid>', 'join_link',
                       display.RHGroupJoinLink)
blueprint.add_url_rule('/registrations/<int:reg_form_id>/group/check-code', 'check_code',
                       display.RHCheckGroupCode)
blueprint.add_url_rule('/registrations/<int:reg_form_id>/group/leave', 'leave_group',
                       display.RHLeaveGroup, methods=('POST',))
blueprint.add_url_rule('/registrations/<int:reg_form_id>/group/new-link', 'regenerate_link',
                       display.RHRegenerateJoinLink, methods=('POST',))
blueprint.add_url_rule('/registrations/<int:reg_form_id>/group/plan', 'switch_plan',
                       display.RHSwitchPlan, methods=('POST',))

# -- management --------------------------------------------------------------

blueprint.add_url_rule('/manage/group-registration/', 'manage_overview',
                       management.RHGroupOverview)
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/', 'manage_groups',
                       management.RHManageGroups)
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/remind', 'remind_forming_groups',
                       management.RHRemindFormingGroups, methods=('POST',))
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/settings', 'manage_settings',
                       management.RHGroupSettings, methods=('GET', 'POST'))
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/balances', 'manage_balances',
                       management.RHGroupBalances)
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/balances/refresh', 'refresh_balance_states',
                       management.RHRefreshBalanceStates, methods=('POST',))
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/<int:group_id>/dissolve', 'dissolve_group',
                       management.RHDissolveGroup, methods=('POST',))
blueprint.add_url_rule('/manage/registration/<int:reg_form_id>/groups/<int:group_id>/reconcile', 'reconcile_group',
                       management.RHReconcileGroup, methods=('POST',))
