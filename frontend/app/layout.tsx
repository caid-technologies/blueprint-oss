import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Blueprint | Build Hardware from Ideas",
  description: "Upload an image or describe an idea to generate parts, wiring, cost, and assembly notes.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
