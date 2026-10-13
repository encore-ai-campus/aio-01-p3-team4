import { useEffect, useState } from 'react';
export function PhotoPreview({ photo }: { photo: File }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const next = URL.createObjectURL(photo); setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [photo]);
  return url ? <img className="photo-preview" src={url} alt="현장 사진"/> : null;
}
