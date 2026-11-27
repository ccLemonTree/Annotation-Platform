export default function PlatformBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="platform-brand" title="图像数据平台">
      <img src="/favicon.svg" width="36" height="36" alt={compact ? '图像数据平台' : ''} />
      {!compact && <span>图像数据平台</span>}
    </div>
  );
}
