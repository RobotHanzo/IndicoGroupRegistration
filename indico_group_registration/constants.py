"""Names shared across modules that must not import each other."""

#: Field type names.  The ``ext__`` prefix is mandatory for plugin-provided
#: registration fields -- Indico's frontend registry rejects anything else.
PLAN_FIELD = 'ext__group_plan'
DISCOUNT_FIELD = 'ext__group_discount'

#: Titles of the auto-provisioned section and fields.  Organizers can rename
#: the section and the plan field; we find them by input type, not by title.
PLAN_FIELD_TITLE = 'Group registration'
DISCOUNT_FIELD_TITLE = 'Group discount'
DISCOUNT_SECTION_TITLE = 'Group discount (internal)'

#: What the participant chose to do on the registration form.
MODE_NONE = 'none'
MODE_CREATE = 'create'
MODE_JOIN = 'join'
MODES = frozenset({MODE_NONE, MODE_CREATE, MODE_JOIN})

MAX_GROUP_NAME_LENGTH = 80
