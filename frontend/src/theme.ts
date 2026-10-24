import type { ThemeConfig } from 'antd'

// Направление «Три шага» с шапкой из направления «Документ»: тёплый серый фон, чёрные основные
// кнопки, жёлтый маркер — как маркер по бумаге: выделенный пункт договора и текущий шаг. Шапка тёмная,
// со светло-синим знаком. Любой текст — не ниже 4,5:1 к своему фону.
export const COLORS = {
  bg: '#F5F3EF',
  surface: '#FFFFFF',
  surface2: '#FAF8F5',
  border: '#E4DFD6',
  borderStrong: '#CFC8BB',
  // Граница полей ввода — 3,3:1 к белому: поле видно и без подписи.
  input: '#958C7E',
  text: '#1F1C17',
  text2: '#57514A',
  text3: '#696157',
  ink: '#1F1C17',
  inkHover: '#3A352D',
  marker: '#FFE27A',
  markerSoft: '#FFF4CC',
  // Тёмно-жёлтый: только полосы хода, не текст.
  markerDeep: '#E0A800',
  danger: '#B3261E',
  dangerSoft: '#FBEAE8',
  warning: '#93530A',
  warningSoft: '#FDF1DC',
  success: '#2F7A4F',
  successSoft: '#E7F3EC',
  // Шапка — тёмно-синяя, знак и текущий раздел — светло-синие (7,7:1). Этот цвет — только в шапке.
  chrome: '#1E293B',
  brandAccent: '#93C5FD',
} as const

export const FONT_UI = '"Onest Variable", "Segoe UI", system-ui, -apple-system, sans-serif'
export const FONT_DOC = '"PT Serif", "Times New Roman", Georgia, serif'

/**
 * Те же цвета и шрифты — переменными CSS (--z-ink, --z-marker-soft…) для своих стилей: один источник,
 * тема Ant Design и styles.css не расходятся.
 */
export function applyCssVariables(root: HTMLElement): void {
  for (const [name, value] of Object.entries(COLORS)) {
    root.style.setProperty(`--z-${name.replace(/[A-Z0-9]/g, (char) => `-${char.toLowerCase()}`)}`, value)
  }
  root.style.setProperty('--z-font-ui', FONT_UI)
  root.style.setProperty('--z-font-doc', FONT_DOC)
}

const INPUT_BORDER = {
  colorBorder: COLORS.input,
  hoverBorderColor: COLORS.text2,
  activeBorderColor: COLORS.ink,
  activeShadow: '0 0 0 2px rgba(31, 28, 23, 0.14)',
}

const SELECT_BORDER = {
  colorBorder: COLORS.input,
  hoverBorderColor: COLORS.text2,
  activeBorderColor: COLORS.ink,
  activeOutlineColor: 'rgba(31, 28, 23, 0.14)',
  optionSelectedBg: COLORS.markerSoft,
  optionSelectedFontWeight: 600,
}

function baseTheme(reducedMotion: boolean): ThemeConfig {
  return {
    token: {
      colorPrimary: COLORS.ink,
      colorInfo: COLORS.ink,
      colorLink: COLORS.ink,
      colorLinkHover: COLORS.inkHover,
      colorLinkActive: COLORS.ink,
      colorSuccess: COLORS.success,
      colorWarning: COLORS.warning,
      colorError: COLORS.danger,
      // Фоны и рамки сообщений и меток — свои: из тёмных основных цветов Ant Design выводит серо-бурые.
      colorSuccessBg: COLORS.successSoft,
      colorSuccessBorder: '#B5D8C2',
      colorWarningBg: COLORS.warningSoft,
      colorWarningBorder: '#EBCB94',
      colorErrorBg: COLORS.dangerSoft,
      colorErrorBorder: '#EDB8B2',
      colorInfoBg: '#F3F0EA',
      colorInfoBorder: COLORS.border,
      colorText: COLORS.text,
      colorTextSecondary: COLORS.text2,
      colorTextTertiary: COLORS.text3,
      colorTextDescription: COLORS.text3,
      colorTextPlaceholder: '#7A7266',
      colorTextHeading: COLORS.text,
      colorBgLayout: COLORS.bg,
      colorBgContainer: COLORS.surface,
      colorBgElevated: COLORS.surface,
      colorBorder: COLORS.borderStrong,
      colorBorderSecondary: COLORS.border,
      colorSplit: COLORS.border,
      colorFillSecondary: '#EFEBE4',
      colorFillTertiary: '#F3F0EA',
      colorFillQuaternary: COLORS.surface2,
      fontFamily: FONT_UI,
      fontSize: 14,
      fontSizeHeading1: 30,
      fontSizeHeading2: 26,
      fontSizeHeading3: 20,
      fontSizeHeading4: 17,
      fontSizeHeading5: 15,
      fontWeightStrong: 600,
      borderRadius: 8,
      borderRadiusLG: 14,
      borderRadiusSM: 6,
      controlHeight: 36,
      controlHeightLG: 44,
      controlHeightSM: 28,
      wireframe: false,
      // Окна, меню и подсказки — быстрее (Эмиль Ковальски: интерфейсу хватает 150–200 мс). «Уменьшить
      // движение» в системе выключает анимации Ant Design совсем.
      motion: !reducedMotion,
      motionDurationFast: '0.1s',
      motionDurationMid: '0.15s',
      motionDurationSlow: '0.2s',
      boxShadowSecondary: '0 6px 24px rgba(31, 28, 23, 0.12), 0 1px 3px rgba(31, 28, 23, 0.08)',
    },
    components: {
      Layout: { headerBg: COLORS.chrome, headerColor: '#EDEAE4', headerHeight: 56, headerPadding: '0 28px', bodyBg: COLORS.bg },
      Button: {
        primaryShadow: 'none',
        defaultShadow: 'none',
        dangerShadow: 'none',
        fontWeight: 600,
        defaultBorderColor: COLORS.borderStrong,
        defaultHoverBorderColor: COLORS.text2,
        defaultHoverColor: COLORS.text,
        primaryColor: '#FFFFFF',
      },
      Card: { headerFontSize: 16 },
      Input: INPUT_BORDER,
      InputNumber: INPUT_BORDER,
      Select: SELECT_BORDER,
      DatePicker: INPUT_BORDER,
      Checkbox: { colorBorder: COLORS.input },
      Radio: { colorBorder: COLORS.input },
      Tabs: { itemColor: COLORS.text2, itemHoverColor: COLORS.text, itemSelectedColor: COLORS.text, inkBarColor: COLORS.ink },
      Table: {
        headerBg: COLORS.surface,
        headerColor: COLORS.text2,
        headerSplitColor: 'transparent',
        rowHoverBg: COLORS.surface2,
        borderColor: COLORS.border,
      },
      Tag: { defaultBg: '#EFEBE4', defaultColor: COLORS.text2 },
      Segmented: { trackBg: '#ECE8E1', itemSelectedBg: COLORS.surface, itemColor: COLORS.text2, itemHoverColor: COLORS.text },
      Menu: {
        itemBg: 'transparent',
        itemColor: COLORS.text2,
        itemHoverColor: COLORS.text,
        itemHoverBg: '#ECE8E1',
        itemSelectedBg: COLORS.surface,
        itemSelectedColor: COLORS.text,
        groupTitleColor: COLORS.text3,
        itemBorderRadius: 10,
      },
      Alert: { withDescriptionPadding: '14px 18px' },
      Modal: { titleFontSize: 18 },
      Drawer: { footerPaddingBlock: 12 },
      Typography: { titleMarginBottom: 0, titleMarginTop: 0 },
    },
  }
}

// На телефоне текст в полях ввода — 16 px: с меньшим шрифтом Safari на iPhone увеличивает страницу при
// нажатии на поле, и после ввода она остаётся сдвинутой. Кнопки и поля выше — под палец.
const MOBILE_COMPONENTS: ThemeConfig['components'] = {
  Input: { ...INPUT_BORDER, inputFontSize: 16 },
  InputNumber: { ...INPUT_BORDER, inputFontSize: 16 },
  DatePicker: { ...INPUT_BORDER, inputFontSize: 16 },
  Select: { ...SELECT_BORDER, fontSize: 16 },
}

export function makeTheme(mobile: boolean, reducedMotion: boolean): ThemeConfig {
  const theme = baseTheme(reducedMotion)
  if (!mobile) return theme
  return {
    ...theme,
    token: { ...theme.token, controlHeight: 40 },
    components: { ...theme.components, ...MOBILE_COMPONENTS },
  }
}
