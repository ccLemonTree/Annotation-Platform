import { useEffect, useState } from 'react';
import { Image } from 'antd';
import type { ImageProps } from 'antd';
import apiClient from '../api/client';

export interface AssetImageSourceOptions {
  assetId?: string;
  imageId?: string;
  contentUrl?: string;
  fullSize?: boolean;
}

export function resolveAssetContentUrl({ assetId, imageId, contentUrl, fullSize = false }: AssetImageSourceOptions): string {
  if (contentUrl) return contentUrl;
  if (imageId) return `/api/images/${encodeURIComponent(imageId)}/content${fullSize ? '' : '?preview=true'}`;
  return `/api/assets/${encodeURIComponent(assetId || '')}/content`;
}

export function useAssetImageUrl({ assetId, imageId, contentUrl, fullSize = false }: AssetImageSourceOptions) {
  const [source, setSource] = useState<string>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!contentUrl && !imageId && !assetId) {
      setSource(undefined);
      setFailed(false);
      return;
    }
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setSource(undefined);
    setFailed(false);
    const url = resolveAssetContentUrl({ assetId, imageId, contentUrl, fullSize });
    apiClient
      .get(url, { responseType: 'blob', signal: controller.signal, skipErrorToast: true })
      .then(response => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(response.data);
        setSource(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId, imageId, contentUrl, fullSize]);

  return { source, failed };
}

export default function AssetImage({
  assetId,
  imageId,
  contentUrl,
  fullSize = false,
  ...props
}: ImageProps & AssetImageSourceOptions) {
  const { source, failed } = useAssetImageUrl({ assetId, imageId, contentUrl, fullSize });
  return failed ? <span>图片加载失败</span> : <Image {...props} src={source} />;
}
