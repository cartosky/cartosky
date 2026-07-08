/**
 * Diff-only, URL-keyed LRU cache for raw frame bytes.
 *
 * Used EXCLUSIVELY by `compare-diff.ts` in v1 — do not import it into
 * `ComparePanel`, `grid-webgl`, or any split-mode path (design doc, Open
 * Decisions — Resolved #6/#7). Frame URLs are already run-scoped, so URL-keying
 * gives implicit invalidation on run change; there is no manual invalidation.
 *
 * The class is intentionally not exported — consume the {@link gridFrameCache}
 * singleton.
 */

// Sized for the diff scrub prefetch window: 2 sides × (active + 4 ahead +
// 2 behind) plus scroll-back history. The byte budget is the real ceiling —
// large ensemble grids (~1.7MB each) evict by size well before the count cap.
const MAX_ENTRIES = 32;
const MAX_BYTES = 50 * 1024 * 1024; // ~50MB

class GridFrameCache {
  // Map iteration order is insertion order; re-inserting on access keeps the
  // most-recently-used entry last, so the first key is always the LRU victim.
  private readonly store = new Map<string, Uint8Array>();
  private totalBytes = 0;

  has(url: string): boolean {
    return this.store.has(url);
  }

  get(url: string): Uint8Array | null {
    const bytes = this.store.get(url);
    if (bytes === undefined) {
      return null;
    }
    // Mark as most-recently-used.
    this.store.delete(url);
    this.store.set(url, bytes);
    return bytes;
  }

  set(url: string, bytes: Uint8Array): void {
    const existing = this.store.get(url);
    if (existing !== undefined) {
      this.totalBytes -= existing.byteLength;
      this.store.delete(url);
    }
    this.store.set(url, bytes);
    this.totalBytes += bytes.byteLength;
    this.evict();
  }

  private evict(): void {
    while (this.store.size > MAX_ENTRIES || this.totalBytes > MAX_BYTES) {
      const oldestKey = this.store.keys().next().value;
      if (oldestKey === undefined) {
        break;
      }
      const oldest = this.store.get(oldestKey);
      if (oldest !== undefined) {
        this.totalBytes -= oldest.byteLength;
      }
      this.store.delete(oldestKey);
      // A single entry larger than the byte budget would loop forever otherwise.
      if (this.store.size === 0) {
        this.totalBytes = 0;
        break;
      }
    }
  }
}

/** Session-scoped in-memory singleton. */
export const gridFrameCache = new GridFrameCache();
