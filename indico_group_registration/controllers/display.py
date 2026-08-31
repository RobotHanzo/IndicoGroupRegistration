"""Participant-facing endpoints.

Everything here acts on the caller's own registration.  There is deliberately
no endpoint that sends mail: leaders share their code and link themselves.
"""

from flask import flash, jsonify, redirect, request, session
from webargs import fields
from werkzeug.exceptions import Forbidden, NotFound

from indico.core.db import db
from indico.modules.events.registration.controllers.display import (RHRegistrationFormBase,
                                                                    RHRegistrationFormRegistrationBase)
from indico.util.i18n import _
from indico.web.args import use_kwargs
from indico.web.flask.util import url_for
from indico.web.rh import RHProtected
from indico.web.util import jsonify_data

from indico_group_registration.operations import GroupError, leave_group, switch_plan
from indico_group_registration.plans import get_plan
from indico_group_registration.util import (find_group_by_uuid, format_code, get_plans, is_enabled,
                                            resolve_join_target)


class RHGroupBase(RHRegistrationFormRegistrationBase):
    """Acts on the caller's own registration and its group."""

    def _process_args(self):
        RHRegistrationFormRegistrationBase._process_args(self)
        if not is_enabled(self.regform):
            raise NotFound
        self.membership = self.registration.group_membership
        if self.membership is None:
            raise NotFound
        self.group = self.membership.group

    def _done(self):
        """Answer one of the group panel's action buttons.

        The buttons are declarative AJAX actions with ``data-reload-after``, so
        the answer has to be JSON.  A redirect would be followed by the AJAX
        request itself, which renders the page -- and consumes the flashed
        message -- somewhere the participant never sees, leaving the reload with
        nothing to show.  ``flash=False`` keeps the message in the session for
        the reload to pick up.
        """
        return jsonify_data(flash=False)


class RHGroupLeaderBase(RHGroupBase):
    """Only the leader may do this."""

    def _check_access(self):
        RHGroupBase._check_access(self)
        if self.group.leader_registration_id != self.registration.id:
            raise Forbidden(_('Only the group leader can do that.'))


class RHCheckGroupCode(RHRegistrationFormBase, RHProtected):
    """Tell the registration form whether a code can be joined.

    Login-only and deliberately terse: this endpoint is otherwise an oracle for
    guessing group codes, so it reveals nothing beyond the name, the seats and
    the plan.
    """

    ALLOW_PROTECTED_EVENT = True

    def _check_access(self):
        RHProtected._check_access(self)
        RHRegistrationFormBase._check_access(self)

    @use_kwargs({'code': fields.String(required=True)}, location='query')
    def _process(self, code):
        if not is_enabled(self.regform):
            raise NotFound
        group, error = resolve_join_target(self.regform, code)
        if error:
            return jsonify({'valid': False, 'error': error})

        plan = group.chosen_plan
        return jsonify({
            'valid': True,
            'name': group.name,
            'code': format_code(group.code),
            'members': group.member_count,
            'target': group.target_size,
            'seats_left': group.seats_left,
            'plan': plan.serialize() if plan else None,
        })


class RHGroupJoinLink(RHRegistrationFormBase):
    """Follow a shared join link.

    The link only pre-fills the code; the person still registers through the
    normal form, with the normal access check and the normal moderation.
    """

    ALLOW_PROTECTED_EVENT = True
    # URL normalization rebuilds this URL from the registration form's locator,
    # which knows nothing about `join_uuid`.  Without preserving it `url_for`
    # raises `BuildError` and every shared join link answers 404.
    normalize_url_spec = {
        'locators': {lambda self: self.regform},
        'preserved_args': {'join_uuid'},
    }

    def _process(self):
        join_uuid = request.view_args['join_uuid']
        group = find_group_by_uuid(self.regform, join_uuid)
        if group is None or not is_enabled(self.regform):
            flash(_('That group link is no longer valid.'), 'error')
            return redirect(url_for('event_registration.display_regform', self.regform))

        existing = self.regform.get_registration(user=session.user) if session.user else None
        if existing is not None:
            if existing.group_membership is None:
                flash(_('You are already registered, so you cannot join a group this way. '
                        'Ask the organizers if you need to be added to one.'), 'warning')
            return redirect(url_for('event_registration.display_regform', existing.locator.registrant))

        _group_error, error = resolve_join_target(self.regform, group.code)
        if error:
            flash(error, 'error')
            return redirect(url_for('event_registration.display_regform', self.regform))

        flash(_('Joining group "{name}". The code has been filled in for you.').format(name=group.name), 'info')
        return redirect(url_for('event_registration.display_regform', self.regform,
                                group_code=group.code))


class RHLeaveGroup(RHGroupBase):
    """Leave the group and go back to the standard rate."""

    def _process(self):
        if self.registration.is_paid:
            flash(_('You have already paid, so you cannot leave the group yourself. '
                    'Please contact the organizers.'), 'error')
            return self._done()
        try:
            leave_group(self.registration)
        except GroupError as exc:
            flash(str(exc), 'error')
            return self._done()
        db.session.commit()
        flash(_('You have left the group. The standard rate now applies.'), 'success')
        return self._done()


class RHRegenerateJoinLink(RHGroupLeaderBase):
    """Replace the join link, revoking every copy already shared."""

    def _process(self):
        from uuid import uuid4
        self.group.join_uuid = str(uuid4())
        db.session.commit()
        flash(_('A new join link has been generated. The old one no longer works.'), 'success')
        return self._done()


class RHSwitchPlan(RHGroupLeaderBase):
    """Change the group's plan while it is still forming and unpaid."""

    @use_kwargs({'plan': fields.String(required=True)})
    def _process(self, plan):
        target = get_plan(get_plans(self.regform), plan)
        if target is None:
            flash(_('Unknown group plan.'), 'error')
            return self._done()
        try:
            switch_plan(self.group, target)
        except GroupError as exc:
            flash(str(exc), 'error')
            return self._done()
        db.session.commit()
        flash(_('Your group is now on the "{label}" plan.').format(label=target.label), 'success')
        return self._done()
