import type { KdsBase, ReactNode } from './_shared'

export type BadgeTone =
  | 'accent' | 'neutral' | 'positive' | 'negative' | 'outline' | 'hottracks' | 'solid'

export interface BadgeProps extends KdsBase {
  type?: 'basic' | 'intermediate' | 'special'
  tone?: BadgeTone
  rank?: number | string
  children?: ReactNode
}
declare function Badge(props: BadgeProps): JSX.Element
export default Badge
