import React, { useEffect, useState } from 'react';
export type Destination = { purpose: 'download' | 'annotation'; projectId: string; collectionId?:string; minLongEdge?: number };
let destination: Destination = { purpose: 'download', projectId: '' };
export function setDestination(value: Destination) { destination = value; }
export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${localStorage.getItem('token') || ''}`);
  const url = String(input);
  const createsData = init.method === 'POST' && /\/(download|download-images|start|create|upload|upload-folder|import-urls|import-existing|configure|jobs)$/.test(url);
  if (createsData) {
    headers.set('X-SmartEye-Purpose', destination.purpose);
    if(destination.collectionId)headers.set('X-SmartEye-Collection',destination.collectionId);
    if(destination.minLongEdge!==undefined)headers.set('X-SmartEye-Min-Long-Edge',String(destination.minLongEdge));
    if (destination.purpose === 'annotation') headers.set('X-SmartEye-Project', destination.projectId);
  }
  return window.fetch(input, { ...init, headers });
}
export async function download(url: string) {
  const local = new URL(url, window.location.origin);
  if (local.origin !== window.location.origin || !local.pathname.startsWith('/api/smarteye/')) {
    window.open(url, '_blank', 'noopener,noreferrer'); return;
  }
  const response = await apiFetch(url);
  if (!response.ok) { alert(`下载失败 (${response.status})`); return; }
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = objectUrl;
  const disposition = response.headers.get('Content-Disposition') || '';
  const utf = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = disposition.match(/filename="?([^";]+)/i);
  link.download = utf ? decodeURIComponent(utf[1]) : plain?.[1] || 'smarteye-download';
  link.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
}
export function AuthImage(props: React.ImgHTMLAttributes<HTMLImageElement>) {
  const [source, setSource] = useState<string>();
  useEffect(() => {
    let alive = true;
    let objectUrl = '';
    const src = props.src || '';
    const url = new URL(src || '/', window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/smarteye/')) {
      setSource(src); return;
    }
    setSource(undefined);
    apiFetch(src).then(async response => {
      if (!response.ok) throw new Error('图片读取失败');
      objectUrl = URL.createObjectURL(await response.blob());
      if (alive) setSource(objectUrl); else URL.revokeObjectURL(objectUrl);
    }).catch(() => { if (alive) setSource(undefined); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [props.src]);
  return React.createElement('img', { ...props, src: source });
}
