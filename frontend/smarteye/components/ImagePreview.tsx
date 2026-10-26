import { Button, Modal } from 'antd';
import { AuthImage, download } from '../network';

export default function ImagePreview({ url, onClose }: { url: string; onClose: () => void }) {
  return <Modal open title="图片预览" onCancel={onClose} width={960}
    footer={<Button onClick={() => void download(url)}>下载 / 打开原图</Button>}>
    <AuthImage src={url} alt="原图预览" style={{ display: 'block', maxWidth: '100%', maxHeight: '70dvh', objectFit: 'contain', margin: '0 auto' }} />
  </Modal>;
}
