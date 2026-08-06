/* KDS 컴포넌트 타입 선언 — `.jsx` 원본은 손대지 않는다(창작 금지).
 *
 * 번들에서 잘라낸 컴포넌트는 순수 JS 라, TS 가 구조분해 인자에서 props 를 추론하면
 * 기본값 없는 인자(style·onRemove·value…)를 전부 **필수**로 잡는다. 원본에 타입을
 * 심는 대신 형제 `.d.ts` 를 둬서 해결한다 — TS 는 `./Button` 을 `Button.d.ts` 로 먼저
 * 해석하므로 `.jsx` 는 그대로 번들에 실리고 타입만 이 파일들이 책임진다.
 */
import type { CSSProperties, ReactNode } from 'react'

export interface KdsBase {
  style?: CSSProperties
  [key: string]: unknown
}
export type { CSSProperties, ReactNode }
