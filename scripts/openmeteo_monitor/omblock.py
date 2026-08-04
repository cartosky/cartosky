import requests

class BlockCachedHTTPFS:
    """fsspec-ish FS fetching fixed-size blocks; counts real bytes transferred."""
    BLOCK = 512 * 1024

    def __init__(self):
        self.bytes = 0
        self.requests = 0
        self.session = requests.Session()
        self._sizes = {}
        self._cache = {}  # (path, block_idx) -> bytes

    def size(self, path):
        if path not in self._sizes:
            r = self.session.head(path, timeout=30)
            r.raise_for_status()
            self._sizes[path] = int(r.headers["Content-Length"])
        return self._sizes[path]

    def _block(self, path, idx):
        key = (path, idx)
        if key not in self._cache:
            start = idx * self.BLOCK
            end = min(start + self.BLOCK, self.size(path)) - 1
            r = self.session.get(path, headers={"Range": f"bytes={start}-{end}"}, timeout=120)
            r.raise_for_status()
            self._cache[key] = r.content
            self.bytes += len(r.content)
            self.requests += 1
        return self._cache[key]

    def cat_file(self, path, start=None, end=None):
        s = start or 0
        e = self.size(path) if end is None else end
        out = bytearray()
        for idx in range(s // self.BLOCK, (e - 1) // self.BLOCK + 1):
            b = self._block(path, idx)
            lo = max(0, s - idx * self.BLOCK)
            hi = min(len(b), e - idx * self.BLOCK)
            out += b[lo:hi]
        return bytes(out)
