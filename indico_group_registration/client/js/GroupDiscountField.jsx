// The group discount field, as the registration form's React side sees it.
//
// The value is the plugin's own bookkeeping: the plugin writes it, the field is
// locked against every other writer, and its only visible form is the named
// line it puts on the invoice. There is nothing here for an organizer to fill
// in, so the field renders no control at all -- only the marker its section is
// hidden by, in `styles/main.scss`.
//
// Registering it with core's field registry at all is what keeps the form
// editor on its feet. `ShowIfInput` builds the "show this field if" dropdown by
// reading `fieldRegistry[inputType].showIfOptions` for *every* item on the
// form, with no guard for an input type that is not in the registry -- so an
// unregistered one threw there and took the whole editor React tree with it,
// leaving a manager who clicked "Configure field" on a blank page.

import React from 'react';

export default function GroupDiscountField() {
  return <span hidden data-group-registration-internal-field="" />;
}
