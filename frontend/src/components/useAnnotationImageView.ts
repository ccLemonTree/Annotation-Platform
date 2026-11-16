import { useState } from 'react';

export type ImageView = 'crop' | 'source';
export default function useAnnotationImageView() {
  return useState<ImageView>('crop');
}
