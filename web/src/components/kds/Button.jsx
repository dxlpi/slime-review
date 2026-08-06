/* KDS — components/actions/Button.jsx (원본 번들 11–107줄). 본문 무수정 — ./README.md 참고. */
import { useState } from 'react'

const H = {
  l: 'var(--kds-field-h-l)',
  m: 'var(--kds-field-h-m)',
  s: 'var(--kds-field-h-s)',
}
const PAD = {
  l: '0 20px',
  m: '0 20px',
  s: '0 16px',
}
const FS = {
  l: '16px',
  m: '16px',
  s: '14px',
}

export default function Button({
  hierarchy = 'primary',
  element = 'box',
  size = 'm',
  icon = null,
  iconRight = null,
  fullWidth = false,
  disabled = false,
  children,
  style,
  ...rest
}) {
  const [pressed, setPressed] = useState(false)
  const base = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 'var(--kds-spacing-200)',
    height: H[size],
    padding: PAD[size],
    fontFamily: 'var(--kds-font-sans)',
    fontSize: FS[size],
    fontWeight: 'var(--kds-weight-medium)',
    letterSpacing: 'var(--kds-tracking)',
    border: '1px solid transparent',
    borderRadius: 'var(--kds-radius-8)',
    cursor: disabled ? 'default' : 'pointer',
    width: fullWidth ? '100%' : 'auto',
    transition:
      'background var(--kds-duration-fast) var(--kds-ease), border-color var(--kds-duration-fast) var(--kds-ease), color var(--kds-duration-fast) var(--kds-ease)',
    whiteSpace: 'nowrap',
  }
  const skin = {
    primary: {
      background: disabled
        ? 'var(--kds-bg-muted)'
        : pressed
          ? 'var(--kds-accent-press)'
          : 'var(--kds-accent)',
      color: disabled ? 'var(--kds-fg-disabled)' : 'var(--kds-fg-on-accent)',
    },
    secondary: {
      background: disabled
        ? 'var(--kds-bg-subtle)'
        : pressed
          ? 'var(--kds-bg-muted)'
          : 'var(--kds-bg)',
      borderColor: disabled ? 'var(--kds-border)' : 'var(--kds-border-strong)',
      color: disabled ? 'var(--kds-fg-disabled)' : 'var(--kds-fg)',
    },
    tertiary: {
      background: pressed ? 'var(--kds-bg-subtle)' : 'transparent',
      color: disabled ? 'var(--kds-fg-disabled)' : 'var(--kds-fg-secondary)',
    },
  }[hierarchy]
  const shape =
    element === 'capsule'
      ? {
          borderRadius: 'var(--kds-radius-round)',
          background: 'var(--kds-bg)',
          borderColor: 'var(--kds-border-strong)',
          color: 'var(--kds-fg)',
          padding: size === 's' ? '0 14px' : '0 18px',
        }
      : element === 'text'
        ? {
            padding: 0,
            height: 'auto',
            background: 'transparent',
            borderColor: 'transparent',
          }
        : element === 'icon'
          ? {
              padding: 0,
              width: H[size],
              borderRadius: 'var(--kds-radius-8)',
            }
          : null

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={() => setPressed(true)}
      onPointerUp={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      style={{ ...base, ...skin, ...shape, ...style }}
      {...rest}
    >
      {icon}
      {element !== 'icon' && children}
      {iconRight}
    </button>
  )
}
