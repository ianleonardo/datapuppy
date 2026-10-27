import * as RS from '@radix-ui/react-select';
import { Children, isValidElement, useState, type ReactElement, type ReactNode } from 'react';

/**
 * Themed drop-in for a native <select>. Native option pop-ups are drawn by the OS (white on macOS/Windows
 * regardless of CSS), so this renders a Radix Select whose list uses the theme surface tokens.
 * Accepts the same <option> children and calls onChange({ target: { value } }) like the native element.
 */
const EMPTY = '__sqa_empty__'; // Radix items cannot use '' as a value

interface Opt {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

function collect(children: ReactNode, out: Opt[] = []): Opt[] {
  Children.forEach(children, (ch) => {
    if (!isValidElement(ch)) return;
    const el = ch as ReactElement<any>;
    if (el.type === 'option') {
      const label = el.props.children;
      const value = el.props.value ?? (typeof label === 'string' ? label : String(label ?? ''));
      out.push({ value: String(value), label, disabled: el.props.disabled });
    } else if (el.props?.children) collect(el.props.children, out);
  });
  return out;
}

export interface SelectProps {
  value?: string | number;
  defaultValue?: string | number;
  onChange?: (e: { target: { value: string } }) => void;
  disabled?: boolean;
  className?: string;
  title?: string;
  id?: string;
  'aria-label'?: string;
  children: ReactNode;
}

export function Select({
  value,
  defaultValue,
  onChange,
  disabled,
  className = 'select',
  title,
  id,
  children,
  ...rest
}: SelectProps) {
  const opts = collect(children);
  const [inner, setInner] = useState(String(defaultValue ?? opts[0]?.value ?? ''));
  const current = value !== undefined ? String(value) : inner;
  const enc = (v: string) => (v === '' ? EMPTY : v);
  return (
    <RS.Root
      value={enc(current)}
      disabled={disabled}
      onValueChange={(v) => {
        const real = v === EMPTY ? '' : v;
        if (value === undefined) setInner(real);
        onChange?.({ target: { value: real } });
      }}
    >
      <RS.Trigger
        id={id}
        className={`${className} sqa-select`}
        title={title}
        aria-label={rest['aria-label']}
      >
        <RS.Value />
        <RS.Icon className="sqa-select-icon" aria-hidden="true">
          ▾
        </RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content className="dd-content sqa-select-content" position="popper" sideOffset={6}>
          <RS.ScrollUpButton className="sqa-select-scroll">▴</RS.ScrollUpButton>
          <RS.Viewport>
            {opts.map((o) => (
              <RS.Item
                key={o.value}
                value={enc(o.value)}
                disabled={o.disabled}
                className="dd-item sqa-select-item"
              >
                <RS.ItemText>{o.label}</RS.ItemText>
                <RS.ItemIndicator className="sqa-select-check">✓</RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
          <RS.ScrollDownButton className="sqa-select-scroll">▾</RS.ScrollDownButton>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  );
}
