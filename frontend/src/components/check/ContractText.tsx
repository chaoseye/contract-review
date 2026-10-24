import { Empty } from 'antd'
import { Fragment, useEffect, useRef } from 'react'

import type { Block } from '../../api/checks.ts'

interface Props {
  blocks: Block[]
  /** Пункт выбранного риска: подсвечен маркером, к нему прокручивается текст. */
  highlighted: string[]
  /** Пункты остальных рисков — светлым маркером. */
  marked?: Set<string>
  /** Меняется при каждом переходе к пункту — вспышка повторяется, даже если пункт тот же. */
  focusKey?: number
  /** Высота панели: рядом с рисками — почти весь экран, в шторке на телефоне — её высота. */
  maxHeight?: string
}

/**
 * Текст договора шрифтом документа. Номера блоков менеджеру не показываются: номер пункта и так
 * в тексте, а у скана — отметка страницы.
 */
export function ContractText({ blocks, highlighted, marked, focusKey = 0, maxHeight = 'calc(100vh - 120px)' }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const items = useRef(new Map<string, HTMLDivElement>())

  useEffect(() => {
    const first = highlighted.find((id) => items.current.has(id))
    const box = container.current
    const target = first ? items.current.get(first) : undefined
    if (!box || !target) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // .z-text — position: relative, поэтому offsetTop блока отсчитывается от начала панели.
    box.scrollTo({ top: target.offsetTop - 24, behavior: reduce ? 'auto' : 'smooth' })
    // Вспышка маркера: глаз находит пункт после прокрутки.
    target.classList.remove('z-flash')
    void target.offsetWidth
    target.classList.add('z-flash')
  }, [highlighted, focusKey])

  if (blocks.length === 0) {
    return <Empty description="Текста договора нет" />
  }
  const active = new Set(highlighted)
  const paged = blocks.some((block) => block.page !== null && block.page > 1)
  return (
    <div ref={container} className="z-text" style={{ maxHeight }}>
      {blocks.map((block, index) => {
        const newPage = paged && block.page !== null && block.page !== blocks[index - 1]?.page
        const classes = ['z-block']
        if (block.kind === 'heading') classes.push('is-heading')
        if (active.has(block.id)) classes.push('is-marked')
        else if (marked?.has(block.id)) classes.push('is-risk')
        return (
          <Fragment key={block.id}>
            {newPage && <div className="z-page-mark">стр. {block.page}</div>}
            <div
              ref={(element) => {
                if (element) items.current.set(block.id, element)
                else items.current.delete(block.id)
              }}
              className={classes.join(' ')}
            >
              {block.text}
            </div>
          </Fragment>
        )
      })}
    </div>
  )
}
