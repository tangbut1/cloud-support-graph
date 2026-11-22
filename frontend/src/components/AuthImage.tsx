import React, { useEffect, useState } from "react";
import { getToken } from "../api/client";

interface AuthImageProps {
  src: string;
  alt?: string;
}

/** 带 Bearer 鉴权的报错截图回看组件（避免将 JWT 拼进 URL 查询串） */
export const AuthImage: React.FC<AuthImageProps> = ({
  src,
  alt = "报错截图",
}) => {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let createdUrl: string | null = null;
    setBlobUrl(null);
    setFailed(false);
    const token = getToken();
    fetch(src, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (!res.ok) throw new Error("load failed");
        return res.blob();
      })
      .then((blob) => {
        if (!active) return;
        createdUrl = URL.createObjectURL(blob);
        setBlobUrl(createdUrl);
      })
      .catch(() => {
        if (active) setFailed(true);
      });

    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [src]);

  if (failed) {
    return (
      <div className="mt-2 text-[11.5px] text-[#9CA3AF]">
        截图附件暂不可加载
      </div>
    );
  }
  if (!blobUrl) {
    return (
      <div className="mt-2 h-24 w-40 rounded bg-[#E5E7EB]/60 animate-pulse" />
    );
  }
  return (
    <a
      href={blobUrl}
      target="_blank"
      rel="noreferrer"
      className="mt-2 inline-block rounded border border-[#D1D5DB] overflow-hidden bg-white"
      title="点击查看大图"
    >
      <img
        src={blobUrl}
        alt={alt}
        className="max-h-44 max-w-full object-contain block"
      />
    </a>
  );
};
