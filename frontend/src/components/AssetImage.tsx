import { useEffect, useState } from 'react';
import { Image } from 'antd';
import type { ImageProps } from 'antd';
import apiClient from '../api/client';

export default function AssetImage({ assetId, imageId, ...props }: ImageProps & { assetId?: string; imageId?: string }) {
  const [source, setSource] = useState<string>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setSource(undefined);
    setFailed(false);
    apiClient.get(`/api/${imageId ? 'images' : 'assets'}/${imageId || assetId}/content`, { responseType: 'blob', signal: controller.signal })
      .then(response => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(response.data);
        setSource(objectUrl);
      })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId, imageId]);

  return failed ? <span>图片加载失败</span> : <Image {...props} src={source} />;
}
