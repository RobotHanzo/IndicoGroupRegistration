"""Group codes: generation, normalization and display."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from indico_group_registration import operations
from indico_group_registration.models.groups import CODE_ALPHABET, CODE_LENGTH, GroupState, generate_code
from indico_group_registration.util import format_code, normalize_code


class TestGenerateCode:
    def test_length_and_alphabet(self):
        for __ in range(200):
            code = generate_code()
            assert len(code) == CODE_LENGTH
            assert set(code) <= set(CODE_ALPHABET)

    @pytest.mark.parametrize('char', 'ILO01')
    def test_ambiguous_characters_are_excluded(self, char):
        """Codes get read aloud and retyped, so these must never appear."""
        assert char not in CODE_ALPHABET

    def test_codes_differ(self):
        assert len({generate_code() for _ in range(100)}) > 90


class TestNormalizeCode:
    @pytest.mark.parametrize('given', (
        'ABCD2345',
        'abcd2345',
        'ABCD-2345',
        'abcd 2345',
        '  ABCD-2345  ',
        'a-b-c-d-2-3-4-5',
    ))
    def test_accepts_however_it_was_pasted(self, given):
        assert normalize_code(given) == 'ABCD2345'

    @pytest.mark.parametrize('given', ('', None))
    def test_empty(self, given):
        assert normalize_code(given) == ''

    def test_drops_characters_outside_the_alphabet(self):
        assert normalize_code('ABCD2345ILO01') == 'ABCD2345'

    def test_truncates_to_the_code_length(self):
        assert normalize_code('ABCD2345EXTRA') == 'ABCD2345'


class TestFormatCode:
    def test_split_in_halves(self):
        assert format_code('ABCD2345') == 'ABCD-2345'

    @pytest.mark.parametrize('given', ('', None, 'SHORT'))
    def test_passes_through_anything_unexpected(self, given):
        assert format_code(given) == (given or '')

    def test_roundtrips_through_normalize(self):
        assert normalize_code(format_code('ABCD2345')) == 'ABCD2345'


class TestGroupState:
    @pytest.mark.parametrize(('state', 'priced'), (
        (GroupState.forming, True),
        (GroupState.confirmed, True),
        (GroupState.short, True),
        (GroupState.dissolved, False),
    ))
    def test_is_priced_by_plan(self, state, priced):
        assert state.is_priced_by_plan is priced

    @pytest.mark.parametrize(('state', 'accepts'), (
        (GroupState.forming, True),
        (GroupState.confirmed, False),
        (GroupState.short, False),
        (GroupState.dissolved, False),
    ))
    def test_accepts_members(self, state, accepts):
        assert state.accepts_members is accepts


class TestRegenerateJoinCredentials:
    """The join link and the code are one credential, so they rotate together.

    `RHGroupJoinLink` redirects whoever follows a link to the registration form
    with `group_code` in the query string, so a link that has been shared is a
    code that has been shared -- browser history, screenshots and proxy logs
    included.  Rotating `join_uuid` alone leaves the panel's promise that every
    link already shared stops working false: nothing else in the plugin ever
    regenerates the code, so the one handed out by the old link still joins.
    """

    ORIGINAL_UUID = '11111111-1111-1111-1111-111111111111'

    @pytest.fixture
    def group(self, monkeypatch):
        """A stand-in group: the rotation is pure enough to test without a database."""
        monkeypatch.setattr(operations, 'lock_group', lambda group: group)
        monkeypatch.setattr(operations, 'db', SimpleNamespace(session=SimpleNamespace(flush=lambda: None)))
        return SimpleNamespace(join_uuid=self.ORIGINAL_UUID, code='ABCD2345', registration_form=object())

    def test_rotates_the_code_with_the_link(self, group, monkeypatch):
        monkeypatch.setattr(operations, '_generate_unique_code', lambda regform: 'WXYZ6789')
        operations.regenerate_join_credentials(group)
        assert group.code == 'WXYZ6789'
        assert group.join_uuid != self.ORIGINAL_UUID
        UUID(group.join_uuid)

    def test_the_new_code_is_allocated_against_the_form(self, group, monkeypatch):
        """Codes are only unique per registration form, so the allocator needs it."""
        asked = []

        def fake_generate(regform):
            asked.append(regform)
            return 'WXYZ6789'

        monkeypatch.setattr(operations, '_generate_unique_code', fake_generate)
        operations.regenerate_join_credentials(group)
        assert asked == [group.registration_form]
