import { Drawer } from 'antd'

import type { AnalysisResult } from '../../api/checks.ts'
import { REFERENCE_LABELS, type ReferenceKey } from './labels.ts'
import { FavorableTab, GapsTab, ObligationsTab, SpecialistsTab } from './SectionsTabs.tsx'

interface Props {
  open: ReferenceKey | null
  result: AnalysisResult
  mobile: boolean
  onClose: () => void
}

/** Справка по договору в шторке: обязательства, противоречия, выгодные условия, вопросы специалистам. */
export function ReferenceDrawer({ open, result, mobile, onClose }: Props) {
  return (
    <Drawer
      title={open ? REFERENCE_LABELS[open] : ''}
      placement={mobile ? 'bottom' : 'right'}
      size={mobile ? '85vh' : 760}
      open={open !== null}
      onClose={onClose}
      destroyOnHidden
      styles={{ body: { padding: mobile ? 12 : 20 } }}
    >
      {open === 'obligations' && <ObligationsTab result={result} />}
      {open === 'gaps' && <GapsTab result={result} />}
      {open === 'favorable' && <FavorableTab result={result} />}
      {open === 'specialists' && <SpecialistsTab result={result} />}
    </Drawer>
  )
}
