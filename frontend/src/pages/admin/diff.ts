// Построчное сравнение двух версий промпта: наибольшая общая подпоследовательность строк.

export interface DiffLine {
  kind: 'same' | 'added' | 'removed'
  text: string
  /** Номер строки в старой и новой версии; у добавленной нет старого, у удалённой — нового. */
  oldNumber: number | null
  newNumber: number | null
}

// Промпт — сотни строк; таблица до 4 млн ячеек считается за доли секунды.
const MAX_CELLS = 4_000_000

export function diffLines(before: string, after: string): DiffLine[] {
  const left = before.split('\n')
  const right = after.split('\n')
  // Общие начало и конец не участвуют в таблице: обычно правка затрагивает несколько строк.
  let start = 0
  while (start < left.length && start < right.length && left[start] === right[start]) start += 1
  let endLeft = left.length
  let endRight = right.length
  while (endLeft > start && endRight > start && left[endLeft - 1] === right[endRight - 1]) {
    endLeft -= 1
    endRight -= 1
  }
  const result: DiffLine[] = []
  for (let index = 0; index < start; index += 1) {
    result.push({ kind: 'same', text: left[index], oldNumber: index + 1, newNumber: index + 1 })
  }
  const a = left.slice(start, endLeft)
  const b = right.slice(start, endRight)
  if (a.length * b.length > MAX_CELLS) {
    a.forEach((text, index) => result.push({ kind: 'removed', text, oldNumber: start + index + 1, newNumber: null }))
    b.forEach((text, index) => result.push({ kind: 'added', text, oldNumber: null, newNumber: start + index + 1 }))
  } else {
    // lengths[i][j] — длина общей подпоследовательности a[i:] и b[j:].
    const width = b.length + 1
    const lengths = new Uint32Array((a.length + 1) * width)
    for (let i = a.length - 1; i >= 0; i -= 1) {
      for (let j = b.length - 1; j >= 0; j -= 1) {
        lengths[i * width + j] =
          a[i] === b[j] ? lengths[(i + 1) * width + j + 1] + 1 : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) {
        result.push({ kind: 'same', text: a[i], oldNumber: start + i + 1, newNumber: start + j + 1 })
        i += 1
        j += 1
      } else if (i < a.length && (j === b.length || lengths[(i + 1) * width + j] >= lengths[i * width + j + 1])) {
        // При равенстве сначала удалённая строка, потом добавленная — как в обычном diff.
        result.push({ kind: 'removed', text: a[i], oldNumber: start + i + 1, newNumber: null })
        i += 1
      } else {
        result.push({ kind: 'added', text: b[j], oldNumber: null, newNumber: start + j + 1 })
        j += 1
      }
    }
  }
  for (let offset = 0; offset < left.length - endLeft; offset += 1) {
    result.push({
      kind: 'same',
      text: left[endLeft + offset],
      oldNumber: endLeft + offset + 1,
      newNumber: endRight + offset + 1,
    })
  }
  return result
}
