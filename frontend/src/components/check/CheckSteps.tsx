import { CheckOutlined, ExclamationOutlined } from '@ant-design/icons'
import type { ReactNode } from 'react'

export type StepKey = 'analysis' | 'risks' | 'protocol'
export type StepState = 'done' | 'current' | 'todo' | 'warn' | 'error'

export interface StepInfo {
  key: StepKey
  title: string
  detail: string
  state: StepState
  /** Шаг можно открыть: «Риски» и «Протокол» после анализа. */
  onOpen?: () => void
}

/**
 * Путь проверки — «Анализ → Риски → Протокол»: что сделано, где менеджер сейчас и что дальше.
 * Справа — переход к следующему шагу; на телефоне его заменяет кнопка внизу экрана.
 */
export function CheckSteps({ steps, action, mobile }: { steps: StepInfo[]; action?: ReactNode; mobile: boolean }) {
  return (
    <nav className={mobile ? 'z-steps z-steps-m' : 'z-steps'} aria-label="Шаги проверки">
      <ol>
        {steps.map((step, index) => {
          const className = `z-step is-${step.state}`
          const current = step.state === 'current' ? 'step' : undefined
          const body = (
            <>
              <span className="z-step-num" aria-hidden="true">
                {step.state === 'done' ? (
                  <CheckOutlined />
                ) : step.state === 'warn' || step.state === 'error' ? (
                  <ExclamationOutlined />
                ) : (
                  index + 1
                )}
              </span>
              <span className="z-step-label">
                <b>{step.title}</b>
                <span>{step.detail}</span>
              </span>
            </>
          )
          return (
            <li key={step.key}>
              {index > 0 && (
                <span className={`z-step-line${steps[index - 1].state === 'done' ? ' is-done' : ''}`} aria-hidden="true" />
              )}
              {step.onOpen && step.state !== 'current' ? (
                <button type="button" className={className} onClick={step.onOpen}>
                  {body}
                </button>
              ) : (
                <div className={className} aria-current={current}>
                  {body}
                </div>
              )}
            </li>
          )
        })}
      </ol>
      {!mobile && action}
    </nav>
  )
}
