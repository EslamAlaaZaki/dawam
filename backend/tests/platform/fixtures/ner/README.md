# NER fixtures (#385)

Inputs (`en.txt`, `ar.txt`) and their expected masked text (`*.masked.txt`) for the
real-engine test in `tests/platform/test_pii_entities.py` (marker `ner`).

| File | Source | Licence |
|---|---|---|
| `en.txt`, `en.masked.txt` | Written for DAWAM | Apache-2.0 (this repository) |
| `ar.txt`, `ar.masked.txt` | Written for DAWAM | Apache-2.0 (this repository) |

The names, street addresses, phone number and email are made up; none is about a real
person. Street names and cities are public place names. The maintainer may swap in their
own documents later; keep the set small (well under 20 MB).
