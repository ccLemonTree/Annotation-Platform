import { ConfigProvider, theme as antdTheme } from 'antd';
import type { ThemeConfig } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { ReactNode } from 'react';
import './tokens.css';

const styles = getComputedStyle(document.documentElement);
const theme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    motion: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    colorPrimary: styles.getPropertyValue('--ui-primary').trim(),
    colorLink: styles.getPropertyValue('--ui-primary').trim(),
    colorBgLayout: styles.getPropertyValue('--ui-background').trim(),
    colorBgBase: styles.getPropertyValue('--ui-background').trim(),
    colorBgContainer: styles.getPropertyValue('--ui-surface').trim(),
    colorBgElevated: styles.getPropertyValue('--ui-surface-raised').trim(),
    colorText: styles.getPropertyValue('--ui-text').trim(),
    colorTextSecondary: styles.getPropertyValue('--ui-muted').trim(),
    colorBorder: styles.getPropertyValue('--ui-border').trim(),
    colorBorderSecondary: styles.getPropertyValue('--ui-border').trim(),
    colorTextPlaceholder: styles.getPropertyValue('--ui-muted').trim(),
    colorSuccess: styles.getPropertyValue('--ui-success').trim(),
    colorWarning: styles.getPropertyValue('--ui-warning').trim(),
    colorError: styles.getPropertyValue('--ui-error').trim(),
    fontFamily: styles.getPropertyValue('--ui-font').trim(),
    fontSize: 14, borderRadius: 8, controlHeight: 36,
  },
  components: {
    Layout: { headerBg: styles.getPropertyValue('--ui-sidebar').trim(), siderBg: styles.getPropertyValue('--ui-sidebar').trim() },
    Menu: { itemBg: styles.getPropertyValue('--ui-sidebar').trim(), itemSelectedBg: styles.getPropertyValue('--ui-primary-soft').trim(), itemSelectedColor: styles.getPropertyValue('--ui-primary').trim(), itemHeight: 44 },
    Card: { headerFontSize: 16, headerHeight: 56, bodyPadding: 24, borderRadiusLG: 12 },
    Table: { headerBg: styles.getPropertyValue('--ui-surface-raised').trim(), rowHoverBg: styles.getPropertyValue('--ui-surface-raised').trim(), cellPaddingBlock: 14, cellPaddingInline: 16 },
    Button: { colorPrimary: styles.getPropertyValue('--ui-action').trim(), colorPrimaryHover: styles.getPropertyValue('--ui-action-hover').trim(), primaryColor: '#ffffff', primaryShadow: 'none', defaultShadow: 'none' },
  },
};

export default function PlatformProvider({ children }: { children: ReactNode }) {
  return <ConfigProvider locale={zhCN} theme={theme}>{children}</ConfigProvider>;
}
