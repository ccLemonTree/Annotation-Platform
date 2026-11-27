import { Button, Space } from 'antd';

export interface CursorPagerProps {
  page: number;
  hasMore: boolean;
  loading?: boolean;
  itemLabel?: string;
  pageDetail?: string;
  inline?: boolean;
  onPrev: () => void;
  onNext: () => void;
}

export function CursorPager({
  page, hasMore, loading = false, itemLabel = '', pageDetail, inline = false, onPrev, onNext,
}: CursorPagerProps) {
  return (
    <nav aria-label={`${itemLabel}分页`} style={inline ? undefined : { display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
      <Space size={8} wrap>
        <Button size="small" disabled={loading || page <= 1} onClick={onPrev}>
          {`上一页${itemLabel}`}
        </Button>
        <span style={{ color: 'var(--ui-muted)', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
          第 {page} 页{pageDetail && ` · ${pageDetail}`}
        </span>
        <Button size="small" disabled={loading || !hasMore} onClick={onNext}>
          {`下一页${itemLabel}`}
        </Button>
      </Space>
    </nav>
  );
}

export default CursorPager;
