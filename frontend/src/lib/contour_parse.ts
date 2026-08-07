// Main-thread client for the contour parse worker. One lazily created worker is
// shared by every contour read (display fetch, prefetch, warm fetch); requests
// are keyed by id so overlapping scrub reads can't cross wires. If workers are
// unavailable (tests, SSR) or the worker dies, later calls parse inline instead;
// requests in flight at the moment of a worker death reject (their transferred
// buffers are detached, so they cannot be re-parsed here).

import type { ContourParseInMessage, ContourParseOutMessage } from "@/lib/contour_parse_worker";

type PendingParse = {
  resolve: (payload: unknown) => void;
  reject: (error: Error) => void;
};

let worker: Worker | null = null;
let workerUnavailable = false;
let nextRequestId = 0;
const pendingParses = new Map<number, PendingParse>();

function failAllPending(message: string): void {
  const pending = [...pendingParses.values()];
  pendingParses.clear();
  for (const entry of pending) {
    entry.reject(new Error(message));
  }
}

function ensureWorker(): Worker | null {
  if (worker) {
    return worker;
  }
  if (workerUnavailable || typeof Worker === "undefined") {
    return null;
  }
  try {
    worker = new Worker(new URL("./contour_parse_worker.ts", import.meta.url), {
      type: "module",
    });
  } catch (error) {
    console.warn("[map] contour parse worker unavailable", { error });
    workerUnavailable = true;
    return null;
  }
  worker.onmessage = (event: MessageEvent<ContourParseOutMessage>) => {
    const message = event.data;
    const pending = pendingParses.get(message.id);
    if (!pending) {
      return;
    }
    pendingParses.delete(message.id);
    if (message.ok) {
      pending.resolve(message.payload);
    } else {
      pending.reject(new Error(message.message));
    }
  };
  worker.onerror = () => {
    // A dead worker would otherwise hang every in-flight parse. The realistic
    // trigger is a module-load failure (stale chunk after a deploy), which
    // repeats on every rebuild — so mark the worker unavailable and let every
    // later call parse inline rather than re-failing forever.
    worker?.terminate();
    worker = null;
    workerUnavailable = true;
    failAllPending("Contour parse worker failed");
  };
  return worker;
}

export async function parseContourResponse(response: Response): Promise<GeoJSON.FeatureCollection> {
  const activeWorker = ensureWorker();
  if (!activeWorker) {
    return (await response.json()) as GeoJSON.FeatureCollection;
  }
  const buffer = await response.arrayBuffer();
  const id = ++nextRequestId;
  const payload = await new Promise<unknown>((resolve, reject) => {
    pendingParses.set(id, { resolve, reject });
    activeWorker.postMessage({ id, buffer } satisfies ContourParseInMessage, [buffer]);
  });
  return payload as GeoJSON.FeatureCollection;
}
