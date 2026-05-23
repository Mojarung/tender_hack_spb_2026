"use client";

import clsx from "clsx";
import { Camera, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { api } from "@/lib/api";
import { DEFAULT_REGION_ID } from "@/lib/regions";
import { history } from "@/lib/history";

interface ImageSearchButtonProps {
  regionId?: number | string;
  className?: string;
  buttonClassName?: string;
  label?: string;
}

export function ImageSearchButton({
  regionId = DEFAULT_REGION_ID,
  className,
  buttonClassName,
  label,
}: ImageSearchButtonProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [loading, setLoading] = useState(false);

  async function onFile(file: File | undefined) {
    if (!file || loading) return;
    setLoading(true);
    try {
      const result = await api.describeImage(file);
      const query = result.query.trim();
      if (!query) throw new Error("Не удалось распознать товар на изображении");
      history.push(query);
      const params = new URLSearchParams({
        q: query,
        region_id: String(regionId || DEFAULT_REGION_ID),
        from_image: "1",
      });
      router.push(`/search?${params.toString()}`);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Не удалось распознать изображение");
    } finally {
      setLoading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <span className={className}>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => void onFile(e.target.files?.[0])}
      />
      <button
        type="button"
        disabled={loading}
        onClick={() => inputRef.current?.click()}
        className={clsx(buttonClassName, loading && "opacity-70 pointer-events-none")}
        title="Найти товар по изображению"
        aria-label="Найти по изображению"
      >
        {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />}
        {label ? <span>{loading ? "Распознаю…" : label}</span> : null}
      </button>
    </span>
  );
}
