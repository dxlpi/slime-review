import type { KdsBase, ReactNode } from './_shared'

export interface ButtonProps extends KdsBase {
  hierarchy?: 'primary' | 'secondary' | 'tertiary'
  element?: 'box' | 'capsule' | 'text' | 'icon'
  size?: 'l' | 'm' | 's'
  icon?: ReactNode
  iconRight?: ReactNode
  fullWidth?: boolean
  disabled?: boolean
  children?: ReactNode
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
}
declare function Button(props: ButtonProps): JSX.Element
export default Button
