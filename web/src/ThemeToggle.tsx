import { useEffect, useState } from "react";

type Mode = "system" | "light" | "dark";
const NEXT: Record<Mode, Mode> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Mode, string> = { system: "Sistem", light: "Terang", dark: "Gelap" };
const ICON: Record<Mode, string> = { system: "◐", light: "☀", dark: "☾" };

function saved(): Mode {
  try {
    const t = localStorage.getItem("theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

export default function ThemeToggle() {
  const [mode, setMode] = useState<Mode>(saved);

  useEffect(() => {
    const root = document.documentElement;
    if (mode === "system") root.removeAttribute("data-theme");
    else root.dataset.theme = mode;
    try {
      if (mode === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", mode);
    } catch {
      /* storage unavailable (private mode) – theme still applies for this session */
    }
  }, [mode]);

  return (
    <button type="button" className="theme-toggle" onClick={() => setMode(NEXT[mode])}
      aria-label={`Tema: ${LABEL[mode]}. Klik untuk mengganti ke ${LABEL[NEXT[mode]]}.`}>
      <span aria-hidden="true">{ICON[mode]}</span> {LABEL[mode]}
    </button>
  );
}
