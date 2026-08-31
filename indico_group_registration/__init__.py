"""Participant-run group registration for Indico.

A participant picks a group plan while registering, gets a code and a join
link, and shares them however they like.  Everyone who joins pays that plan's
rate straight away.  The group locks itself the moment the plan's seat count is
filled; if it never fills, the plugin reprices it at the reconciliation
deadline and reports what each member still owes.
"""

from indico.util.i18n import make_bound_gettext


__version__ = '0.2.1'

_ = make_bound_gettext('group_registration')
