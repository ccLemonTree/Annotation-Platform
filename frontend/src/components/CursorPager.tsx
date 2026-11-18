import { Button, Space } from 'antd';

export interface CursorPagerProps {
  page: number;
  hasMore: boolean;
  loading?: boolean;
  onPrev: () => void;
  onNext: () => void;
}

export function CursorPager({ page, hasMore, loading = false, onPrev, onNext }: CursorPagerProps) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
      <Space size={8}>
        <Button size="small" disabled={loading || page <= 1} onClick={onPrev}>
          上一页
        </Button>
        <span style={{ color: 'var(--ui-muted)', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
          第 {page} 页
        </span>
        <Button size="small" disabled={loading || !hasMore} onClick={onNext}>
          下一页
        </Button>
      </Space>
    </div>
  );
}

export default CursorPager;
