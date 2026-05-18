import { useState } from 'react';
import { displayableImageUrl } from '../lib/images';

type ProductImageProps = {
  imageUrl?: string | null;
  alt: string;
  frameClassName: string;
};

export default function ProductImage({ imageUrl, alt, frameClassName }: ProductImageProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const displayUrl = displayableImageUrl(imageUrl);

  if (!displayUrl || displayUrl === failedUrl) {
    return null;
  }

  return (
    <div className={frameClassName}>
      <img
        src={displayUrl}
        alt={alt}
        className="h-full w-full object-contain"
        onError={() => setFailedUrl(displayUrl)}
      />
    </div>
  );
}
