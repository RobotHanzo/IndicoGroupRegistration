"""The join link's uuid segment is whatever the caller put in the URL."""

import pytest

from indico_group_registration.util import find_group_by_uuid


class _Regform:
    """Enough of a registration form to build the query -- which must not happen."""

    id = 42


@pytest.mark.parametrize('given', (
    'x',
    '',
    'not-a-uuid',
    'a1b2c3d4-e5f6-7890-abcd-ef012345678',   # one digit short
    "' OR 1=1 --",
    None,
))
def test_unparseable_uuid_is_simply_no_group(given):
    """It has to answer `None`, and answer it without asking the database.

    `join_uuid` is a Postgres `uuid` column, so a non-UUID comparand aborts the
    statement with `DataError` rather than matching nothing: the participant
    gets a 500 instead of the "that group link is no longer valid" flash, and
    the operator gets the traceback in the post.  There is no session here, so
    reaching the query at all fails this test.
    """
    assert find_group_by_uuid(_Regform(), given) is None
