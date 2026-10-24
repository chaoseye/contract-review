import '@fontsource-variable/onest'
import '@fontsource/pt-serif/400.css'
import '@fontsource/pt-serif/700.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { Root } from './Root.tsx'
import './styles.css'
import { applyCssVariables } from './theme.ts'

const root = document.getElementById('root')
if (root === null) {
  throw new Error('Не найден элемент #root')
}
applyCssVariables(document.documentElement)

createRoot(root).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
