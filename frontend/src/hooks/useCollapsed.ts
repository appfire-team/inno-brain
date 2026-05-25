import { useCallback, useEffect, useState } from "react";

/** Persistent boolean state. Keyed in localStorage so collapsed sidebars
 *  stay collapsed across reloads. */
export function useCollapsed(key: string, initial = false): [boolean, () => void] {
  const storageKey = `innobrain.collapsed.${key}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return initial;
    const raw = window.localStorage.getItem(storageKey);
    if (raw == null) return initial;
    return raw === "1";
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, collapsed ? "1" : "0");
    } catch {
      /* ignore quota errors */
    }
  }, [collapsed, storageKey]);

  const toggle = useCallback(() => setCollapsed((c) => !c), []);
  return [collapsed, toggle];
}
