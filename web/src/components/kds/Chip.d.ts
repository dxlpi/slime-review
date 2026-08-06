import type { KdsBase, ReactNode } from './_shared'

export interface ChipProps extends KdsBase {
  type?: 'basic' | 'input' | 'anchor'
  selected?: boolean
  disabled?: boolean
  onRemove?: () => void
  children?: ReactNode
  onClick?: (e: React.MouseEvent) => void
  href?: string
}
declare function Chip(props: ChipProps): JSX.Element
export default Chip
