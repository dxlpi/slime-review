import type { KdsBase } from './_shared'

export type TabItem = string | { value: string; label?: string }

export interface TabProps extends KdsBase {
  items?: readonly TabItem[]
  value?: string
  onChange?: (value: string) => void
  variant?: 'primary' | 'secondary'
  fill?: boolean
}
declare function Tab(props: TabProps): JSX.Element
export default Tab
