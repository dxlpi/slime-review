import type { KdsBase } from './_shared'

export interface IconProps extends KdsBase {
  name: string
  size?: number
  color?: string
  strokeWidth?: number
}
declare function Icon(props: IconProps): JSX.Element
export default Icon
