"""Alternate fetch sources that feed the shared build pipeline.

Each sub-package is a *fetch source*, not a pipeline: it produces arrays on a
production target grid and hands them to the existing packing / sidecar /
manifest / promotion machinery unchanged.
"""
