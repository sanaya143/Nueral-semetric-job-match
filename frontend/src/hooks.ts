import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

/** GET `path` (null = skip). `poll` re-fetches every N ms while `until(data)` is false. */
export function useApi<T>(path: string | null, opts: { poll?: number; until?: (d: T) => boolean } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const timer = useRef<number | null>(null);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const d = await get<T>(path);
      setData(d);
      setError(null);
      const { poll, until } = optsRef.current;
      if (poll && until && !until(d)) timer.current = window.setTimeout(load, poll);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    load();
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [load]);

  return { data, error, loading, reload: load, setData };
}
