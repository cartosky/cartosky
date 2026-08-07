/// <reference lib="webworker" />
// Contour GeoJSON parse worker. Contour payloads are large enough that
// JSON.parse on the main thread stalls the frame that a timeline scrub is
// trying to draw. The main thread reads the response body (so auth, abort and
// the fetch metric all stay where they are) and hands the raw bytes over here;
// this worker only decodes + parses and posts the object back.

export type ContourParseInMessage = { id: number; buffer: ArrayBuffer };

export type ContourParseOutMessage =
  | { id: number; ok: true; payload: unknown }
  | { id: number; ok: false; message: string };

const scope = self as unknown as DedicatedWorkerGlobalScope;
const decoder = new TextDecoder();

scope.onmessage = (event: MessageEvent<ContourParseInMessage>) => {
  const { id, buffer } = event.data;
  try {
    const payload = JSON.parse(decoder.decode(new Uint8Array(buffer))) as unknown;
    scope.postMessage({ id, ok: true, payload } satisfies ContourParseOutMessage);
  } catch (error) {
    scope.postMessage({
      id,
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    } satisfies ContourParseOutMessage);
  }
};
