import type { KdsBase, ReactNode } from './_shared'

export interface InputProps extends KdsBase {
  field?: 'text' | 'textarea'
  size?: 'l' | 'm' | 's'
  state?: 'default' | 'focused' | 'filled' | 'disabled' | 'error' | 'success' | 'autocomplete'
  label?: ReactNode
  placeholder?: string
  value?: string
  onChange?: (e: React.ChangeEvent<HTMLInputElement & HTMLTextAreaElement>) => void
  helperText?: ReactNode
  rows?: number
}
declare function Input(props: InputProps): JSX.Element
export default Input
