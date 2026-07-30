import { afterEach, describe, expect, it } from "vitest";

import { buildComparePermalinkSearch, readComparePermalink } from "@/lib/compare-permalink";
import { buildPermalinkSearch } from "@/lib/permalink";
import { readPermalink } from "@/lib/permalink-read";

/**
 * Phase 2B domain threading. The `domain` param is conditionally serialized on
 * BOTH permalink writers: a canonical (null/absent) domain must produce the
 * exact same query string it did before the param existed, so shared links and
 * server-side screenshot render URLs stay byte-identical for existing users.
 */

/**
 * Both readers take no argument — they read `window.location.search` directly
 * — so the node-env round-trips drive them through a minimal location stub.
 */
function withSearch<T>(search: string, read: () => T): T {
  const previous = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { location: { search } };
  try {
    return read();
  } finally {
    if (previous === undefined) {
      delete (globalThis as { window?: unknown }).window;
    } else {
      (globalThis as { window?: unknown }).window = previous;
    }
  }
}

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
});

const VIEWER_BASE = {
  model: "gfs",
  run: "2026072700",
  var: "tmp2m",
  fh: 24,
  lat: 39.83,
  lon: -98.58,
  z: 4,
};

const COMPARE_BASE = {
  lm: "gfs",
  lv: "tmp2m",
  lr: "2026072700",
  rm: "gfs",
  rv: "tmp2m",
  rr: "latest",
  fh: 24,
  lat: 39.83,
  lon: -98.58,
  z: 4,
};

describe("viewer permalink domain serialization", () => {
  it("serializes a present domain and round-trips it", () => {
    const search = buildPermalinkSearch({ ...VIEWER_BASE, domain: "global" });
    expect(new URLSearchParams(search).get("domain")).toBe("global");
    expect(withSearch(search, readPermalink).domain).toBe("global");
  });

  it("omits the param entirely for a canonical/absent domain", () => {
    const canonical = buildPermalinkSearch({ ...VIEWER_BASE, domain: undefined });
    expect(canonical).not.toContain("domain");
    expect(withSearch(canonical, readPermalink).domain).toBeUndefined();
    // Byte-identical to a state that never knew about the param.
    expect(canonical).toBe(buildPermalinkSearch({ ...VIEWER_BASE }));
  });
});

describe("compare permalink domain serialization", () => {
  it("serializes a present domain and round-trips it", () => {
    const search = buildComparePermalinkSearch({ ...COMPARE_BASE, domain: "global" });
    expect(new URLSearchParams(search).get("domain")).toBe("global");
    expect(withSearch(search, readComparePermalink).domain).toBe("global");
  });

  it("omits the param entirely for a canonical/absent domain", () => {
    const canonical = buildComparePermalinkSearch({ ...COMPARE_BASE, domain: undefined });
    expect(canonical).not.toContain("domain");
    expect(withSearch(canonical, readComparePermalink).domain).toBeUndefined();
    expect(canonical).toBe(buildComparePermalinkSearch({ ...COMPARE_BASE }));
  });

  it("lowercases a deep-linked domain on read", () => {
    expect(withSearch("?domain=GLOBAL", readComparePermalink).domain).toBe("global");
  });
});

describe("viewer -> compare handoff", () => {
  /**
   * Mirrors the compareHref memo in App.tsx: the viewer hands its RESOLVED
   * data domain (null for canonical) to the compare page, which reads a single
   * `domain` param shared by both panes.
   */
  function compareHref(dataDomain: string | null): string {
    return `/compare${buildComparePermalinkSearch({ ...COMPARE_BASE, domain: dataDomain || undefined })}`;
  }

  it("carries a resolved domain into the compare URL", () => {
    const href = compareHref("global");
    const search = new URLSearchParams(href.slice(href.indexOf("?")));
    expect(search.get("domain")).toBe("global");
    // And the compare page reads back exactly what the viewer sent.
    expect(withSearch(href.slice(href.indexOf("?")), readComparePermalink).domain).toBe("global");
  });

  it("emits no domain param when the domain resolves to canonical", () => {
    expect(compareHref(null)).not.toContain("domain");
  });
});
