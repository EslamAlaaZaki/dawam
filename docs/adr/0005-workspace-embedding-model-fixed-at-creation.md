# A Workspace's embedding model is fixed at creation

Each Workspace chooses its own embedding model in the "New workspace" form, pre-selected with the installation's embedding model, and cannot change it afterwards in this release. Document vectors made by one model are not comparable with another's (a different dimension breaks search outright; the same dimension silently returns meaningless matches), so changing the model would need a per-Workspace re-index we are not building yet. For the same reason an admin cannot remove an embedding model while any Workspace has documents indexed with it; with no indexed documents it is replaced like any other model. A later release may allow changing it together with a re-index.

## Considered options

- **Re-index on change**: keeps search correct, but needs a per-Workspace re-indexing job and a text-only fallback while it runs; deferred.
- **Replace without re-index**: rejected, it breaks or silently degrades document search.
- **One installation-wide embedding model**: simpler, but switching it would re-index every Workspace at once and lets no Workspace choose an internal model of its own.
