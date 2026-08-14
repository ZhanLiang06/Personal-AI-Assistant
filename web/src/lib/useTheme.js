import { useCallback, useEffect, useState } from "react";

const THEME_KEY = "kairos-theme";
const MODE_KEY = "kairos-mode";

function read(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

/**
 * Owns the two independent axes of the skin: which world (telemetry /
 * edgerunner) and which mode (light / dark). Both live on <html> so CSS can
 * key off them and the pre-paint script in index.html can restore them.
 */
export function useTheme() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme ?? read(THEME_KEY, "telemetry"),
  );
  const [mode, setMode] = useState(
    () => document.documentElement.dataset.mode ?? read(MODE_KEY, "dark"),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.mode = mode;
    try {
      localStorage.setItem(THEME_KEY, theme);
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      // Preference just will not persist. Nothing else breaks.
    }
  }, [theme, mode]);

  const toggleMode = useCallback(
    () => setMode((current) => (current === "dark" ? "light" : "dark")),
    [],
  );

  return { theme, mode, setTheme, toggleMode };
}
