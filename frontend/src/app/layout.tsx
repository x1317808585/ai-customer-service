import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI 客服",
  description: "电商 AI 客服",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="h-full bg-white text-gray-900">{children}</body>
    </html>
  );
}
