import type { Metadata } from "next";
import { Alegreya_Sans, Fraunces, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

// next/font downloads these at build time and serves them from the app; the browser never calls Google.
const sans = Alegreya_Sans({ variable: "--font-alegreya", subsets: ["latin"], weight: ["400", "500", "700"] });
const mono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500"] });
const serif = Fraunces({ variable: "--font-fraunces", subsets: ["latin"], axes: ["SOFT", "opsz"], style: ["normal", "italic"] });

export const metadata: Metadata = {
  title: "Vario",
  description: "Local dashboard for Claude Code sessions with voice in and out",
};

// Runs before paint: the saved theme, else the system's. ThemeToggle keeps it in sync after that.
const themeInit = `try{var t=localStorage.getItem("vario-theme")||localStorage.getItem("pallas-theme");if(t!=="light"&&t!=="dark")t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="light"}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable} ${serif.variable} h-full antialiased`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="h-full bg-ground font-sans text-fg">{children}</body>
    </html>
  );
}
