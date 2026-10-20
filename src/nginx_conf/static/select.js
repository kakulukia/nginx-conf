(() => {
  if (CSS.supports('appearance', 'base-select') && CSS.supports('selector(::picker(select))')) return;

  document.querySelectorAll('select.nc-input').forEach((select, number) => {
    if (select.multiple || select.size > 1 || !select.options.length) return;
    const labels = Array.from(select.labels);
    const name = select.getAttribute('aria-label') || labels.map(label => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll('select, input, textarea, button').forEach(control => control.remove());
      return copy.textContent.trim();
    }).join(' ');
    const id = `${select.id || `nc-select-${number}`}-trigger`;
    const wrapper = document.createElement('span');
    wrapper.className = 'nc-select';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = id;
    trigger.className = 'nc-input nc-select__trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-label', name);
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-controls', `${id}-options`);
    const value = document.createElement('span');
    trigger.append(value);
    const list = document.createElement('span');
    list.className = 'nc-suggestions';
    list.id = `${id}-options`;
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', name);
    list.hidden = true;
    const error = document.createElement('span');
    error.className = 'nc-error';
    error.id = `${id}-error`;
    error.setAttribute('role', 'alert');
    error.hidden = true;
    const parentLabel = select.closest('label');
    select.before(wrapper);
    wrapper.append(trigger, list, error);
    if (parentLabel) parentLabel.after(select);
    labels.forEach(label => label.htmlFor = id);
    let active = -1;
    let query = '';
    let typedAt = 0;
    const options = () => Array.from(select.options);
    const enabled = index => {
      const option = select.options[index];
      return Boolean(option && !option.disabled && !option.parentElement.disabled);
    };
    const close = () => {
      list.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.removeAttribute('aria-activedescendant');
      query = '';
      typedAt = 0;
    };
    const sync = () => {
      value.textContent = select.selectedOptions[0]?.label || 'Bitte auswählen';
      trigger.disabled = select.disabled;
      trigger.toggleAttribute('data-placeholder', !select.value);
      trigger.setAttribute('aria-required', String(select.required));
      trigger.setAttribute('aria-describedby', [select.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '));
      if (select.validity.valid) error.hidden = true;
      const invalid = select.getAttribute('aria-invalid') || (!error.hidden ? 'true' : null);
      if (invalid) trigger.setAttribute('aria-invalid', invalid);
      else trigger.removeAttribute('aria-invalid');
      if (select.disabled) close();
    };
    const highlight = index => {
      active = index;
      Array.from(list.children).forEach((item, position) => item.toggleAttribute('data-active', position === active));
      if (active >= 0) {
        trigger.setAttribute('aria-activedescendant', list.children[active].id);
        list.children[active].scrollIntoView({block: 'nearest'});
      }
    };
    const open = () => {
      if (select.matches(':disabled')) return;
      list.replaceChildren();
      options().forEach((option, index) => {
        const item = document.createElement('span');
        item.id = `${id}-option-${index}`;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.setAttribute('aria-disabled', String(!enabled(index)));
        item.toggleAttribute('data-placeholder', !option.value);
        const check = document.createElement('span');
        check.className = 'nc-select__check';
        check.setAttribute('aria-hidden', 'true');
        check.textContent = option.selected ? '✓' : '';
        item.append(check, document.createTextNode(option.label));
        list.append(item);
      });
      list.hidden = false;
      const bounds = trigger.getBoundingClientRect();
      const below = window.innerHeight - bounds.bottom;
      const above = below < Math.min(192, list.scrollHeight) && bounds.top > below;
      wrapper.toggleAttribute('data-open-above', above);
      list.style.maxHeight = `${Math.max(40, Math.min(192, (above ? bounds.top : below) - 8))}px`;
      trigger.setAttribute('aria-expanded', 'true');
      highlight(enabled(select.selectedIndex) ? select.selectedIndex : options().findIndex((_, index) => enabled(index)));
    };
    const choose = index => {
      if (!enabled(index)) return;
      const changed = select.selectedIndex !== index;
      select.selectedIndex = index;
      close();
      sync();
      if (changed) {
        select.dispatchEvent(new Event('input', {bubbles: true}));
        select.dispatchEvent(new Event('change', {bubbles: true}));
      }
    };
    trigger.addEventListener('click', () => list.hidden ? open() : close());
    trigger.addEventListener('blur', close);
    trigger.addEventListener('keydown', event => {
      if (event.isComposing) return;
      if (event.key === 'Escape') {
        if (!list.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
        return;
      }
      if (event.key === 'Tab') { if (!list.hidden) choose(active); close(); return; }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (list.hidden) open(); else choose(active);
        return;
      }
      const indices = options().map((_, index) => index).filter(enabled);
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        if (list.hidden) { open(); if (event.key.startsWith('Arrow')) return; }
        if (!indices.length) return;
        const position = indices.indexOf(active);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? indices.length - 1
          : (position + (event.key === 'ArrowDown' ? 1 : indices.length - 1)) % indices.length;
        highlight(indices[next]);
      } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        query = Date.now() - typedAt > 700 ? event.key : query + event.key;
        typedAt = Date.now();
        if (list.hidden) open();
        const repeated = Array.from(query).every(character => character === query[0]);
        const start = repeated ? indices.indexOf(active) + 1 : 0;
        const ordered = [...indices.slice(start), ...indices.slice(0, start)];
        const search = repeated ? event.key : query;
        const match = ordered.find(index => select.options[index].label.toLocaleLowerCase().startsWith(search.toLocaleLowerCase()));
        if (match !== undefined) highlight(match);
      }
    });
    list.addEventListener('pointerdown', event => event.preventDefault());
    list.addEventListener('click', event => {
      event.preventDefault();
      const item = event.target.closest('[role="option"]');
      if (item) choose(Array.from(list.children).indexOf(item));
    });
    document.addEventListener('pointerdown', event => { if (!wrapper.contains(event.target)) close(); });
    select.addEventListener('change', sync);
    select.addEventListener('invalid', event => {
      event.preventDefault();
      error.textContent = select.validationMessage;
      error.hidden = false;
      sync();
      trigger.focus();
    });
    select.form?.addEventListener('reset', () => queueMicrotask(() => { close(); sync(); }));
    new MutationObserver(sync).observe(select, {attributes: true, attributeFilter: ['disabled', 'required', 'aria-invalid', 'aria-describedby']});
    select.hidden = true;
    sync();
  });
})();
