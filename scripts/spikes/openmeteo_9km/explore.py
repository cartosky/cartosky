from omfiles import OmFileReader
from omhttp import CountingHTTPFS

fs = CountingHTTPFS()
url = "https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs/2026/08/04/1200Z/2026-08-04T1200.om"
print("file size:", fs.size(url) / 1e6, "MB")
r = OmFileReader.from_fsspec(fs, url)
print("root: group=", r.is_group, "children=", r.num_children)
for i in range(r.num_children):
    c = r.get_child_by_index(i)
    if c.is_array:
        print(f"  {c.name}: shape={c.shape} chunks={c.chunks} dtype={c.dtype} comp={c.compression_name}")
    else:
        print(f"  {c.name}: scalar/group children={c.num_children}")
print("bytes for open+metadata:", fs.bytes, "requests:", fs.requests)
