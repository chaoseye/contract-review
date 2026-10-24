import { Flex, Segmented, Typography, theme } from 'antd'
import { useMemo, useState } from 'react'

import { diffLines } from './diff.ts'

interface Props {
  before: string
  after: string
}

// Строки без изменений вокруг правки: сколько показывать в режиме «Только изменения».
const CONTEXT = 3

/** Сравнение двух текстов по строкам: удалённые — красным, добавленные — зелёным. */
export function DiffView({ before, after }: Props) {
  const { token } = theme.useToken()
  const [mode, setMode] = useState<'changes' | 'all'>('changes')
  const lines = useMemo(() => diffLines(before, after), [before, after])
  const added = lines.filter((line) => line.kind === 'added').length
  const removed = lines.filter((line) => line.kind === 'removed').length

  const visible = useMemo(() => {
    if (mode === 'all') return lines.map((line) => ({ line, gap: false }))
    const keep = new Set<number>()
    lines.forEach((line, index) => {
      if (line.kind !== 'same') {
        for (let near = Math.max(0, index - CONTEXT); near <= Math.min(lines.length - 1, index + CONTEXT); near += 1) {
          keep.add(near)
        }
      }
    })
    const shown: { line: (typeof lines)[number]; gap: boolean }[] = []
    let previous = -1
    lines.forEach((line, index) => {
      if (!keep.has(index)) return
      shown.push({ line, gap: previous >= 0 && index > previous + 1 })
      previous = index
    })
    return shown
  }, [lines, mode])

  if (added === 0 && removed === 0) {
    return <Typography.Text type="secondary">Тексты совпадают.</Typography.Text>
  }
  const colors = {
    added: { background: token.colorSuccessBg, sign: '+' },
    removed: { background: token.colorErrorBg, sign: '−' },
    same: { background: 'transparent', sign: ' ' },
  }
  return (
    <Flex vertical gap={8}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Text type="secondary">
          Добавлено строк: {added}, удалено: {removed}
        </Typography.Text>
        <Segmented
          size="small"
          value={mode}
          onChange={setMode}
          options={[
            { value: 'changes', label: 'Только изменения' },
            { value: 'all', label: 'Весь текст' },
          ]}
        />
      </Flex>
      <div
        style={{
          fontFamily: token.fontFamilyCode,
          fontSize: 12,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: token.borderRadius,
          maxHeight: '60vh',
          overflow: 'auto',
        }}
      >
        {visible.map(({ line, gap }, index) => (
          <div key={index}>
            {gap && <div style={{ color: token.colorTextTertiary, padding: '2px 8px' }}>⋯</div>}
            <div style={{ display: 'flex', background: colors[line.kind].background, whiteSpace: 'pre-wrap' }}>
              <span style={{ width: 44, flex: 'none', textAlign: 'right', color: token.colorTextTertiary, paddingRight: 6 }}>
                {line.oldNumber ?? ''}
              </span>
              <span style={{ width: 44, flex: 'none', textAlign: 'right', color: token.colorTextTertiary, paddingRight: 6 }}>
                {line.newNumber ?? ''}
              </span>
              <span style={{ width: 16, flex: 'none' }}>{colors[line.kind].sign}</span>
              <span style={{ wordBreak: 'break-word' }}>{line.text || ' '}</span>
            </div>
          </div>
        ))}
      </div>
    </Flex>
  )
}
