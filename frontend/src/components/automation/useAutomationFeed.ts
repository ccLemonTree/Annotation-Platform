import { useEffect, useState } from 'react';

// Schedule after completion so a slow request never overlaps the next poll.
export default function useAutomationFeed<T>(key: string, load: (signal: AbortSignal) => Promise<T>, keepPolling: (data: T) => boolean, revision: string | number = 0) {
  const [snapshot, setSnapshot] = useState<{ key: string; data: T }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      setLoading(true); setError(false);
      try {
        const data = await load(controller.signal);
        if (controller.signal.aborted) return;
        setSnapshot({ key, data }); setError(false);
        if (keepPolling(data)) timer = setTimeout(() => { void run(); }, 3000);
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void run();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [key, load, keepPolling, revision]);
  return { data: snapshot?.key === key ? snapshot.data : undefined, loading, error };
}
