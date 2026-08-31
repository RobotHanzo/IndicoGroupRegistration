// Copy buttons for the group code and join link.
//
// The panel is server-rendered Jinja, so this is plain DOM work rather than
// React. It degrades to a selectable read-only input if the clipboard API is
// unavailable.

import {Translate} from 'indico/react/i18n';

function copy(input, button) {
  const text = input.dataset.copyValue || input.value;
  const done = () => {
    const original = button.textContent;
    button.textContent = Translate.string('Copied');
    button.disabled = true;
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1500);
  };

  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(done, () => input.select());
  } else {
    input.select();
    input.setSelectionRange(0, input.value.length);
  }
}

export default function setupGroupPanel() {
  const inputs = document.querySelectorAll('.group-registration-share__value');
  inputs.forEach(input => {
    input.addEventListener('focus', () => input.select());

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'i-button icon-copy group-registration-share__copy';
    button.textContent = Translate.string('Copy');
    button.addEventListener('click', () => copy(input, button));
    input.insertAdjacentElement('afterend', button);
  });
}
