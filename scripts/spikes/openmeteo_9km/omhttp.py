import requests

class CountingHTTPFS:
    """Duck-typed fsspec-ish filesystem over HTTP with byte/request accounting."""
    def __init__(self):
        self.bytes = 0
        self.requests = 0
        self.session = requests.Session()
        self._sizes = {}

    def size(self, path):
        if path not in self._sizes:
            r = self.session.head(path, timeout=30)
            r.raise_for_status()
            self._sizes[path] = int(r.headers["Content-Length"])
        return self._sizes[path]

    def cat_file(self, path, start=None, end=None):
        headers = {}
        if start is not None or end is not None:
            s = start or 0
            e = "" if end is None else end - 1  # HTTP ranges are inclusive
            headers["Range"] = f"bytes={s}-{e}"
        r = self.session.get(path, headers=headers, timeout=120)
        r.raise_for_status()
        data = r.content
        self.bytes += len(data)
        self.requests += 1
        return data
