/* KDS — components/core/Icon.jsx (원본 번들 108–163줄)
 *
 * 원본 주석 그대로:
 *   Stand-in icon renderer: KDS publishes only raster icon specs, so the catalogue
 *   renders Lucide (24px grid, uniform stroke, round caps/joins) from CDN.
 *
 * ⚠️ CDN 전역(`window.lucide`) 폴링을 npm import 로 바꾼 유일한 파일이다 — 사유는 ./README.md.
 */
import { useEffect, useRef } from 'react'
import { createIcons, icons } from 'lucide'

export default function Icon({
  name,
  size = 24,
  color = 'currentColor',
  strokeWidth = 1.6,
  style,
  ...rest
}) {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.innerHTML = '<i data-lucide="' + name + '"></i>'
    createIcons({
      icons,
      root: ref.current,
      attrs: {
        width: size,
        height: size,
        stroke: color,
        'stroke-width': strokeWidth,
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
      },
    })
  }, [name, size, color, strokeWidth])

  return (
    <span
      ref={ref}
      aria-hidden="true"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        flex: 'none',
        ...style,
      }}
      {...rest}
    />
  )
}
