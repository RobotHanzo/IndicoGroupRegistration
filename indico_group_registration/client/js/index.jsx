// Registers this plugin's field types with the registration form's field
// registry, wires up the copy buttons on the group panel, and corrects the
// invoice box for a member who owes a repricing balance.
//
// Both field types have to be registered, including the internal one. The form
// editor's `ShowIfInput` looks every item's input type up in the registry
// without checking that it is there, so a field type the server puts on the
// form and the client does not know about is not a cosmetic problem -- it
// crashes the editor.

import {registerPluginObject} from 'indico/utils/plugins';

import setupBalanceBadge from './balanceBadge';
import GroupDiscountField from './GroupDiscountField';
import GroupPlanInput from './GroupPlanInput';
import setupGroupPanel from './groupPanel';

// Plugin field names must start with `ext__`; the core registry rejects
// anything else.
registerPluginObject('group_registration', 'regformCustomFields', {
  name: 'ext__group_plan',
  title: 'Group registration',
  icon: 'users',
  inputComponent: GroupPlanInput,
  // We render our own layout (radio list, disclaimer, code lookup) rather than
  // a labelled control in the standard two-column grid.
  customFormItem: true,
  noLabel: true,
  noRequired: true,
  noRetentionPeriod: true,
});

registerPluginObject('group_registration', 'regformCustomFields', {
  name: 'ext__group_discount',
  // The registry asks every field for a title and an icon, for the "Add field"
  // dropdown -- which this one is then kept out of: the plugin provisions the
  // field itself, and a second copy would be a second invoice line that nothing
  // ever writes to.
  title: 'Group discount (internal)',
  icon: 'coins',
  hideFromItemDropdown: () => true,
  inputComponent: GroupDiscountField,
  // It draws its own (empty) form item rather than a labelled control, and none
  // of the standard field settings apply to a value only the plugin ever
  // writes.
  customFormItem: true,
  noLabel: true,
  noRequired: true,
  noRetentionPeriod: true,
  noInternalName: true,
});

document.addEventListener('DOMContentLoaded', () => {
  setupGroupPanel();
  setupBalanceBadge();
});
