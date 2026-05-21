"use client";
import { Toaster as HotToaster } from "react-hot-toast";

export function Toaster() {
  return (
    <HotToaster
      position="bottom-center"
      gutter={8}
      toastOptions={{
        duration: 2600,
        style: {
          background: "var(--color-ink)",
          color: "white",
          fontSize: "0.875rem",
          borderRadius: "12px",
          padding: "10px 14px",
          boxShadow: "0 12px 24px rgba(0,0,0,0.18)",
        },
        success: { iconTheme: { primary: "#10b981", secondary: "white" } },
        error:   { iconTheme: { primary: "#ef4444", secondary: "white" } },
      }}
    />
  );
}
