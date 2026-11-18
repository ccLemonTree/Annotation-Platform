import { computeSha256Sync } from './sha256.worker';

export { computeSha256Sync };

interface PendingHash {
  resolve: (hash: string) => void;
  reject: (err: Error) => void;
}

let sharedWorker: Worker | null | false = null;
let nextRequestId = 1;
const pending = new Map<number, PendingHash>();

function getHashWorker(): Worker | null {
  if (sharedWorker === false || typeof Worker === 'undefined') return null;
  if (sharedWorker) return sharedWorker;
  try {
    const worker = new Worker(new URL('./sha256.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<{ id: number; hash?: string; error?: string }>) => {
      const entry = pending.get(event.data.id);
      if (!entry) return;
      pending.delete(event.data.id);
      if (event.data.error) entry.reject(new Error(event.data.error));
      else entry.resolve(event.data.hash || '');
    };
    worker.onerror = () => {
      sharedWorker = false;
      for (const [id, entry] of pending.entries()) {
        pending.delete(id);
        entry.reject(new Error('SHA-256 Worker failed'));
      }
      worker.terminate();
    };
    sharedWorker = worker;
    return worker;
  } catch {
    sharedWorker = false;
    return null;
  }
}

export async function sha256(input: ArrayBuffer): Promise<string> {
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', input);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }

  // LAN HTTP lacks Web Crypto; offload pure-JS SHA-256 to a Web Worker so large archive uploads never freeze the UI thread.
  const worker = getHashWorker();
  if (worker) {
    try {
      const copy = input.slice(0);
      return await new Promise<string>((resolve, reject) => {
        const id = nextRequestId++;
        pending.set(id, { resolve, reject });
        worker.postMessage({ id, buffer: copy }, [copy]);
      });
    } catch {
      // Fallback to synchronous computation if the worker cannot run in current environment.
    }
  }

  return computeSha256Sync(input);
}
