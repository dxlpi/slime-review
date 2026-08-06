/* KDS — components/forms/Input.jsx (원본 번들 907–1021줄). 본문 무수정 — ./README.md 참고. */
import { useState } from 'react'
import Icon from './Icon'

const H = {
  l: 'var(--kds-field-h-l)',
  m: 'var(--kds-field-h-m)',
  s: 'var(--kds-field-h-s)',
}

export default function Input({
  field = 'text',
  size = 'm',
  state = 'default',
  label,
  placeholder = '',
  value,
  onChange,
  helperText,
  rows = 4,
  style,
  ...rest
}) {
  const [focused, setFocused] = useState(false)
  const st = focused && state === 'default' ? 'focused' : state
  const borderColor = {
    default: 'var(--kds-border-strong)',
    focused: 'var(--kds-accent)',
    filled: 'var(--kds-border-strong)',
    disabled: 'var(--kds-border)',
    error: 'var(--kds-negative)',
    success: 'var(--kds-positive)',
    autocomplete: 'var(--kds-accent)',
  }[st]
  const helperColor =
    st === 'error'
      ? 'var(--kds-negative)'
      : st === 'success'
        ? 'var(--kds-positive)'
        : 'var(--kds-fg-tertiary)'
  const shared = {
    width: '100%',
    fontFamily: 'var(--kds-font-sans)',
    fontSize: 'var(--kds-text-xl-size)',
    letterSpacing: 'var(--kds-tracking)',
    color: st === 'disabled' ? 'var(--kds-fg-disabled)' : 'var(--kds-fg)',
    background: st === 'disabled' ? 'var(--kds-bg-subtle)' : 'var(--kds-bg)',
    border: '1px solid ' + borderColor,
    borderRadius: 'var(--kds-radius-8)',
    padding: field === 'textarea' ? '12px 16px' : '0 16px',
    outline: 'none',
    transition: 'border-color var(--kds-duration-fast) var(--kds-ease)',
  }

  return (
    <label style={{ display: 'block', ...style }}>
      {label && (
        <span
          style={{
            display: 'block',
            marginBottom: 'var(--kds-spacing-200)',
            fontSize: 'var(--kds-text-l-size)',
            fontWeight: 'var(--kds-weight-medium)',
            color: 'var(--kds-fg-secondary)',
          }}
        >
          {label}
        </span>
      )}
      <span style={{ position: 'relative', display: 'block' }}>
        {field === 'textarea' ? (
          <textarea
            rows={rows}
            placeholder={placeholder}
            value={value}
            onChange={onChange}
            disabled={st === 'disabled'}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            style={{ ...shared, resize: 'vertical', lineHeight: 'var(--kds-text-xl-line)' }}
            {...rest}
          />
        ) : (
          <input
            placeholder={placeholder}
            value={value}
            onChange={onChange}
            disabled={st === 'disabled'}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            style={{
              ...shared,
              height: H[size],
              paddingRight: st === 'error' || st === 'success' ? 44 : 16,
            }}
            {...rest}
          />
        )}
        {field === 'text' && (st === 'error' || st === 'success') && (
          <span
            style={{
              position: 'absolute',
              right: 14,
              top: 0,
              height: H[size],
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <Icon
              name={st === 'error' ? 'alert-circle' : 'check-circle'}
              size={20}
              color={st === 'error' ? 'var(--kds-negative)' : 'var(--kds-positive)'}
            />
          </span>
        )}
      </span>
      {helperText && (
        <span
          style={{
            display: 'block',
            marginTop: 'var(--kds-spacing-100)',
            fontSize: 'var(--kds-text-m-size)',
            lineHeight: 'var(--kds-text-m-line)',
            color: helperColor,
          }}
        >
          {helperText}
        </span>
      )}
    </label>
  )
}
