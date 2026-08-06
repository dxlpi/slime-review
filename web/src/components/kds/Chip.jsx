/* KDS — components/actions/Chip.jsx (원본 번들 164–231줄). 본문 무수정 — ./README.md 참고. */
import Icon from './Icon'

export default function Chip({
  type = 'basic',
  selected = false,
  disabled = false,
  onRemove,
  children,
  style,
  ...rest
}) {
  const Tag = type === 'anchor' ? 'a' : 'button'
  const label =
    typeof children === 'string' && children.length > 20 ? children.slice(0, 20) : children

  return (
    <Tag
      {...(Tag === 'button' ? { type: 'button', disabled } : {})}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--kds-spacing-100)',
        height: 'var(--kds-field-h-s)',
        padding: type === 'input' ? '0 8px 0 14px' : '0 16px',
        maxWidth: 220,
        borderRadius: 'var(--kds-radius-round)',
        border: '1px solid ' + (selected ? 'var(--kds-gray-900)' : 'var(--kds-border)'),
        background: selected ? 'var(--kds-gray-900)' : 'var(--kds-bg)',
        color: disabled
          ? 'var(--kds-fg-disabled)'
          : selected
            ? 'var(--kds-fg-on-accent)'
            : 'var(--kds-fg-secondary)',
        fontFamily: 'var(--kds-font-sans)',
        fontSize: 'var(--kds-text-l-size)',
        fontWeight: selected ? 'var(--kds-weight-medium)' : 'var(--kds-weight-regular)',
        letterSpacing: 'var(--kds-tracking)',
        textDecoration: 'none',
        cursor: disabled ? 'default' : 'pointer',
        transition:
          'background var(--kds-duration-fast) var(--kds-ease), border-color var(--kds-duration-fast) var(--kds-ease)',
        ...style,
      }}
      {...rest}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      {type === 'input' && (
        <span
          role="button"
          aria-label="삭제"
          onClick={(e) => {
            e.stopPropagation()
            onRemove && onRemove()
          }}
          style={{ display: 'inline-flex', padding: 2, cursor: 'pointer', opacity: 0.7 }}
        >
          <Icon name="x" size={14} />
        </span>
      )}
      {type === 'anchor' && <Icon name="chevron-right" size={14} />}
    </Tag>
  )
}
