# A Workspace's embedding model may change: its documents are embedded again

Supersedes ADR 0005.

An owner may change a Workspace's embedding model at any time, also after documents are indexed. Vectors made by different models are not comparable, so a change with indexed documents starts a background `reindex_documents` job that embeds every document again with the new model. While it runs, document search matches text only: the vector ranking is switched off for the Workspace, so no query is answered from the old model's vectors or from a partly rebuilt set. When the job ends, one transaction drops every vector not made by the new model and turns the vector ranking back on: the switch is atomic, and results never mix models.

The switch happens however the job ends. A document whose embedding fails (or the whole job, cancelled or lost) stays text only, as an embedding failure on upload already does, and a later re-index fills it in; search does not stay text only indefinitely. A newer change, or a manual re-index, supersedes a rebuild that is still running: only the newest one switches. An admin still cannot remove a model, or take the embedding role off it, while a Workspace has documents indexed with it; after the switch, the old model has none.

## Considered options

- **Keep the model fixed after indexing (ADR 0005)**: safe, but an owner who picked the wrong model, or needs a multilingual one, must start a new Workspace.
- **Keep the old vectors searchable until the switch**: search stays semantic during the rebuild, but the new vectors need their own column or table until then (one row per model per passage), which doubles vector storage and every write path; rejected for now.
- **Switch file by file**: no full-text-only window, but results mix two models' scores while the rebuild runs; rejected.
