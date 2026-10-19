// Keep pasted or autofilled codes as six ASCII digits, including leading zeroes.
document.querySelectorAll('.nc-otp__input').forEach(input => {
  const slots = Array.from(input.nextElementSibling.children);
  const showPosition = () => {
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const first = start === end ? Math.max(0, end - 1) : start;
    const last = Math.max(first, end - 1);
    slots.forEach((slot, index) => {
      slot.toggleAttribute('data-active', document.activeElement === input && index >= first && index <= last);
    });
  };
  // Place the insertion point after a clicked digit so Backspace removes it.
  input.addEventListener('click', event => {
    if (event.detail === 1 && !event.shiftKey && input.selectionStart === input.selectionEnd) {
      const index = slots.findIndex(slot => event.clientX <= slot.getBoundingClientRect().right);
      const position = Math.min((index < 0 ? 5 : index) + 1, input.value.length);
      input.setSelectionRange(position, position);
    }
    showPosition();
  });
  ['focus', 'blur', 'keyup', 'select'].forEach(event => input.addEventListener(event, showPosition));
  input.addEventListener('input', () => {
    const position = input.selectionStart;
    const prefix = input.value.slice(0, position).replace(/[^0-9]/g, '');
    input.value = input.value.replace(/[^0-9]/g, '').slice(0, 6);
    input.setSelectionRange(prefix.length, prefix.length);
    showPosition();
  });
  input.addEventListener('paste', event => {
    event.preventDefault();
    const digits = event.clipboardData.getData('text').replace(/[^0-9]/g, '');
    if (!digits) return;
    input.setRangeText(digits, input.selectionStart, input.selectionEnd, 'end');
    input.dispatchEvent(new Event('input', {bubbles: true}));
  });
  input.classList.add('nc-otp__input--enhanced');
});
