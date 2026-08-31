// Registers the plan picker with the registration form's field registry and
// wires up the copy buttons on the group panel.

import {registerPluginObject} from 'indico/utils/plugins';

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

document.addEventListener('DOMContentLoaded', setupGroupPanel);
