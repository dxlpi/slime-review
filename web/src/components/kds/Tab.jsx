/* KDS — components/navigation/Tab.jsx (원본 번들 1315–1378줄). 본문 무수정 — ./README.md 참고. */

export default function Tab({
  items = [],
  value,
  onChange,
  variant = 'primary',
  fill = true,
  style,
  ...rest
}) {
  const primary = variant === 'primary'

  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        width: '100%',
        borderBottom: primary ? 'var(--kds-hairline)' : 'none',
        gap: primary ? 0 : 'var(--kds-spacing-200)',
        ...style,
      }}
      {...rest}
    >
      {items.map((it) => {
        const v = it.value ?? it,
          l = it.label ?? it
        const on = v === value
        return (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange && onChange(v)}
            style={
              primary
                ? {
                    flex: fill ? '1 1 0' : 'none',
                    height: 48,
                    padding: '0 12px',
                    background: 'transparent',
                    border: 'none',
                    borderBottom: '2px solid ' + (on ? 'var(--kds-gray-900)' : 'transparent'),
                    marginBottom: -1,
                    cursor: 'pointer',
                    fontFamily: 'var(--kds-font-sans)',
                    fontSize: 'var(--kds-text-xl-size)',
                    letterSpacing: 'var(--kds-tracking)',
                    fontWeight: on ? 'var(--kds-weight-bold)' : 'var(--kds-weight-regular)',
                    color: on ? 'var(--kds-fg)' : 'var(--kds-fg-tertiary)',
                    transition: 'color var(--kds-duration-fast) var(--kds-ease)',
                  }
                : {
                    height: 'var(--kds-field-h-s)',
                    padding: '0 16px',
                    borderRadius: 'var(--kds-radius-round)',
                    border: '1px solid ' + (on ? 'var(--kds-gray-900)' : 'var(--kds-border)'),
                    background: on ? 'var(--kds-gray-900)' : 'var(--kds-bg)',
                    color: on ? 'var(--kds-fg-on-accent)' : 'var(--kds-fg-secondary)',
                    fontFamily: 'var(--kds-font-sans)',
                    fontSize: 'var(--kds-text-l-size)',
                    cursor: 'pointer',
                  }
            }
          >
            {l}
          </button>
        )
      })}
    </div>
  )
}
