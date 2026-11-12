"use client";

import type { ReactNode } from "react";

import { DESKTOP_RELEASE_URL } from "@/lib/site";

export const DESKTOP_INSTALL_WARNING =
  "Windows will warn that this installer is unrecognized. Choose More info, then Run anyway.";

export function DesktopDownloadLink({
  className,
  children,
  newTab = false,
}: {
  className?: string;
  children: ReactNode;
  newTab?: boolean;
}) {
  return (
    <a
      href={DESKTOP_RELEASE_URL}
      className={className}
      target={newTab ? "_blank" : undefined}
      rel={newTab ? "noreferrer" : undefined}
      onClick={(event) => {
        if (!window.confirm(DESKTOP_INSTALL_WARNING)) event.preventDefault();
      }}
    >
      {children}
    </a>
  );
}
