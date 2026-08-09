import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Prosperity — Autopilot Indian F&O signals on your Tradejini account",
  description:
    "Connect your Tradejini account. Our AI brain trades the signal; your account sizes the risk. One brain, your money, your control.",
  metadataBase: new URL("https://app.diffraction.in"),
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
