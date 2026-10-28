import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";

import "./globals.css";

export const metadata: Metadata = {
  title: "合意",
  description: "合意电商客服",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className={`${GeistSans.variable} h-full antialiased`}>
      <body className="h-full">{children}</body>
    </html>
  );
}
