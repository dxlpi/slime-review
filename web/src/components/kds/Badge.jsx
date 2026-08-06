/* KDS — components/display/Badge.jsx (원본 번들 282–375줄). 본문 무수정 — ./README.md 참고.
 * ⚠️ tone="accent" 의 blue-800 글자색은 우리 민트 액센트와 충돌한다(README 의 '알려진 충돌'). */

const TONES = {
  accent: {
    bg: 'var(--kds-accent-subtle)',
    fg: 'var(--kds-blue-800)',
    bd: 'transparent',
  },
  neutral: {
    bg: 'var(--kds-bg-muted)',
    fg: 'var(--kds-fg-secondary)',
    bd: 'transparent',
  },
  positive: {
    bg: 'var(--kds-green-100)',
    fg: 'var(--kds-green-800)',
    bd: 'transparent',
  },
  negative: {
    bg: 'var(--kds-red-100)',
    fg: 'var(--kds-red-800)',
    bd: 'transparent',
  },
  outline: {
    bg: 'var(--kds-bg)',
    fg: 'var(--kds-fg-secondary)',
    bd: 'var(--kds-border)',
  },
  hottracks: {
    bg: 'var(--kds-hottracks)',
    fg: 'var(--kds-fg-on-accent)',
    bd: 'transparent',
  },
  solid: {
    bg: 'var(--kds-accent)',
    fg: 'var(--kds-fg-on-accent)',
    bd: 'transparent',
  },
}

export default function Badge({ type = 'basic', tone = 'neutral', rank, children, style, ...rest }) {
  const t = TONES[tone] || TONES.neutral
  const pad = type === 'intermediate' ? 6 : 4

  if (type === 'special') {
    return (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--kds-spacing-100)',
          height: 'var(--kds-badge-h)',
          padding: '0 8px',
          borderRadius: 'var(--kds-radius-4)',
          background: 'var(--kds-gray-900)',
          color: 'var(--kds-fg-on-accent)',
          fontSize: 'var(--kds-text-m-size)',
          fontWeight: 'var(--kds-weight-bold)',
          letterSpacing: 'var(--kds-tracking)',
          ...style,
        }}
        {...rest}
      >
        {rank != null && (
          <span style={{ color: 'var(--kds-green-400)', fontWeight: 'var(--kds-weight-bold)' }}>
            {rank}
          </span>
        )}
        {children}
      </span>
    )
  }

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 'var(--kds-badge-h)',
        padding: '0 ' + pad + 'px',
        borderRadius: 'var(--kds-radius-4)',
        background: t.bg,
        color: t.fg,
        border: '1px solid ' + t.bd,
        fontSize: 'var(--kds-text-m-size)',
        fontWeight: 'var(--kds-weight-medium)',
        letterSpacing: 'var(--kds-tracking)',
        whiteSpace: 'nowrap',
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  )
}
