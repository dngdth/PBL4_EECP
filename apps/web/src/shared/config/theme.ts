/**
 * Centralized Design Token System
 *
 * Defines semantic color tokens, layout dimensions, border radii,
 * typography scales, and shadows for the PBL4 control center.
 *
 * All components should reference semantic tokens or Tailwind semantic utility classes
 * rather than hardcoded hex color codes.
 */

export const THEME_TOKENS = {
  colors: {
    // Canvas & Surface
    background: 'var(--color-background, #F7F5F0)',
    surface: 'var(--color-surface, #FFFFFF)',
    surfaceSubtle: 'var(--color-surface-subtle, #FAF8F5)',
    surfaceMuted: 'var(--color-surface-muted, #F0ECE1)',

    // Typography
    text: 'var(--color-text, #252525)',
    textMuted: 'var(--color-text-muted, #6F6A63)',
    textSubtle: 'var(--color-text-subtle, #A49E95)',

    // Borders & Dividers
    border: 'var(--color-border, #E5E0D8)',
    borderSubtle: 'var(--color-border-subtle, #EFECE6)',
    borderDark: 'var(--color-border-dark, #DDD8CF)',

    // Brand / Primary Action
    primary: 'var(--color-primary, #E8752A)',
    primaryDark: 'var(--color-primary-dark, #C95716)',
    primarySoft: 'var(--color-primary-soft, #FDF2EB)',

    // Status: Success / Nominal
    success: 'var(--color-success, #4F8A62)',
    successDark: 'var(--color-success-dark, #2F6B43)',
    successSoft: 'var(--color-success-soft, #EBF5EE)',

    // Status: Warning / Attention
    warning: 'var(--color-warning, #D99A22)',
    warningDark: 'var(--color-warning-dark, #8C5D08)',
    warningSoft: 'var(--color-warning-soft, #FEF6E6)',

    // Status: Error / Critical Failed
    error: 'var(--color-error, #C94C4C)',
    errorDark: 'var(--color-error-dark, #B32E2E)',
    errorSoft: 'var(--color-error-soft, #FDF0F0)',

    // Amber / Accent
    amber: 'var(--color-amber, #E9B949)',
    amberSoft: 'var(--color-amber-soft, #FCF7E8)',
  },

  layout: {
    sidebarExpandedWidth: '16rem', // 256px
    sidebarCollapsedWidth: '4rem', // 64px
    sidebarMobileWidth: '18rem', // 288px
    headerHeight: '3.5rem', // 56px (h-14)
    headerHeightLg: '5rem', // 80px (min-h-20)
  },

  radius: {
    xs: '0.125rem', // 2px (rounded-xs)
    sm: '0.25rem', // 4px (rounded-sm)
    md: '0.375rem', // 6px (rounded-md)
    lg: '0.5rem', // 8px (rounded-lg)
    full: '9999px',
  },

  fonts: {
    sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    mono: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
  },

  typography: {
    /**
     * Page level header (e.g. Session Title, Main Dashboard Title)
     * Font: sans, 20px–24px (1.25rem–1.5rem), font-bold, leading-tight
     */
    pageTitle: 'text-xl sm:text-2xl font-bold font-sans tracking-tight text-text leading-tight',

    /**
     * Section headings (e.g. MÁY TRẠM, CHÍNH SÁCH BẢO MẬT, HOẠT ĐỘNG GẦN ĐÂY)
     * Font: sans, 13px–14px, uppercase, font-bold, tracking-wider
     */
    sectionTitle: 'text-xs sm:text-sm font-bold uppercase tracking-wider font-sans text-text',

    /**
     * Card title (e.g. Policy name, Modal dialog title)
     * Font: sans, 14px–16px, font-bold
     */
    cardTitle: 'text-sm sm:text-base font-bold font-sans text-text',

    /**
     * Primary body text (messages, descriptions, form labels)
     * Font: sans, 14px (0.875rem), leading-relaxed
     */
    body: 'text-sm font-normal font-sans text-text leading-relaxed',

    /**
     * Secondary body text (sub-descriptions, helper notes)
     * Font: sans, 12px–13px, leading-normal
     */
    bodySmall: 'text-xs sm:text-[13px] font-normal font-sans text-text-muted leading-normal',

    /**
     * Metadata & Field Labels (e.g. PHÒNG THI, GATEWAY)
     * Font: sans, 12px, font-semibold, uppercase, tracking-wider
     */
    label: 'text-xs font-semibold font-sans uppercase tracking-wider text-text-muted',

    /**
     * Smaller uppercase label (badges, secondary tags)
     * Font: sans, 11px–12px, font-bold, uppercase
     */
    labelSmall: 'text-[11px] sm:text-xs font-bold font-sans uppercase tracking-wider text-text-muted',

    /**
     * Technical Data (workstation IDs, IP, Session ID, Policy ID)
     * Font: mono, 13px–14px, font-bold/font-medium
     */
    mono: 'text-xs sm:text-sm font-mono font-medium text-text',

    /**
     * Technical Metadata (timestamps, hashes, port numbers, log timestamps)
     * Font: mono, 12px
     */
    monoSmall: 'text-xs font-mono text-text-muted',

    /**
     * Large Monospace (Main Technical Header, e.g. Session ID code, KPI big digits)
     * Font: mono, 16px–24px, font-bold
     */
    monoHeader: 'text-base sm:text-lg font-bold font-mono text-text',
  },
} as const;

export type ThemeTokens = typeof THEME_TOKENS;
