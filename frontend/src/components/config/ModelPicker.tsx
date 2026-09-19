import { useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { ChevronDown } from 'lucide-react';

export interface PickerOption {
  id: string;
  source: 'catalog' | 'configured';
}

const MAX_SHOWN = 80;

/**
 * Free-text model ID input with a searchable candidate list.
 *
 * Candidates come from the fetched provider catalog and existing config; any
 * typed value is accepted, so unlisted or brand-new IDs still work.
 */
export function ModelPicker({
  label,
  value,
  options,
  onChange,
  disabled,
  invalid,
  describedBy,
  placeholder = '输入或选择模型 ID',
}: {
  label: string;
  value: string;
  options: PickerOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  placeholder?: string;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Only narrow the list while the user is typing; opening the picker on an
  // already-filled field must still show every candidate.
  const [typing, setTyping] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const query = typing ? value.trim().toLowerCase() : '';
    const list = query
      ? options.filter((option) => option.id.toLowerCase().includes(query))
      : options;
    return list.slice(0, MAX_SHOWN);
  }, [options, value, typing]);

  const choose = (id: string) => {
    onChange(id);
    setOpen(false);
    setActive(-1);
    setTyping(false);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!open) setOpen(true);
      setActive((index) => Math.min(index + 1, filtered.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, -1));
    } else if (event.key === 'Enter') {
      if (open && active >= 0 && filtered[active]) {
        event.preventDefault();
        choose(filtered[active].id);
      }
    } else if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        setOpen(false);
        setActive(-1);
      }
    }
  };

  return (
    <div
      className="mc-picker"
      ref={root}
      onBlur={(event) => {
        if (!root.current?.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
          setActive(-1);
          setTyping(false);
        }
      }}
    >
      <input
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={
          open && active >= 0 ? `${listId}-${active}` : undefined
        }
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        maxLength={256}
        onChange={(event) => {
          onChange(event.target.value);
          setTyping(true);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => {
          setTyping(false);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className="mc-picker-toggle"
        tabIndex={-1}
        aria-label={`${label} 候选列表`}
        disabled={disabled}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          setTyping(false);
          setOpen((state) => !state);
        }}
      >
        <ChevronDown size={14} />
      </button>
      {open && !disabled && (
        <ul
          className="mc-picker-list"
          role="listbox"
          id={listId}
          aria-label={`${label} 候选`}
        >
          {filtered.map((option, index) => (
            <li
              key={option.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={index === active ? 'mc-active' : undefined}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(option.id)}
            >
              <span>{option.id}</span>
              <small>{option.source === 'catalog' ? '目录' : '已配置'}</small>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="mc-picker-empty" aria-disabled="true">
              {options.length
                ? '没有匹配的候选；可直接使用输入的 ID。'
                : '尚无候选；先在提供商中获取模型列表，或直接输入 ID。'}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
