"""The real ``EntityDetector`` (#385): Presidio, with spaCy for English and CAMeL Tools for
Arabic, all self-hosted. Needs the optional ``ner`` extra and the two models (the spaCy
package, e.g. ``python -m spacy download en_core_web_sm``, and the CAMeL BERT NER model, a
Hugging Face id fetched once into the local cache, or a folder). Text never leaves the
server: both models run in this process.

A text is analysed in both languages; English spans must hold a Latin letter and Arabic
spans an Arabic one, so neither model's guesses on the other script get through.
"""

from __future__ import annotations

import re
from typing import Any

from dawam.platform.pii_entities import EntityKind, EntitySpan

_KINDS: dict[str, EntityKind] = {"PERSON": "person", "LOCATION": "location"}
_LATIN = re.compile(r"[A-Za-z]")
_ARABIC = re.compile(r"[؀-ۿ]")
MIN_SCORE = 0.5
CHUNK_TOKENS = 200
"""Arabic words per CAMeL call: well under BERT's 512 word pieces."""


class PresidioEntityDetector:
    def __init__(self, *, english_model: str, arabic_model: str) -> None:
        import spacy
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import SpacyNlpEngine
        from presidio_analyzer.predefined_recognizers import SpacyRecognizer

        engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": english_model}])
        engine.nlp = {"en": spacy.load(english_model), "ar": spacy.blank("ar")}
        registry = RecognizerRegistry(supported_languages=["en", "ar"])
        registry.add_recognizer(
            SpacyRecognizer(supported_language="en", supported_entities=list(_KINDS))
        )
        registry.add_recognizer(_camel_recognizer(arabic_model))
        self._analyzer = AnalyzerEngine(
            registry=registry, nlp_engine=engine, supported_languages=["en", "ar"]
        )

    def detect(self, text: str) -> list[EntitySpan]:
        spans: list[EntitySpan] = []
        for language, script in (("en", _LATIN), ("ar", _ARABIC)):
            if not script.search(text):
                continue
            for found in self._analyzer.analyze(
                text, language=language, entities=list(_KINDS), score_threshold=MIN_SCORE
            ):
                if script.search(text, found.start, found.end):
                    spans.append(EntitySpan(found.start, found.end, _KINDS[found.entity_type]))
        return spans


def _camel_recognizer(model: str) -> Any:
    from camel_tools.ner import NERecognizer
    from camel_tools.tokenizers.word import simple_word_tokenize
    from presidio_analyzer import EntityRecognizer, RecognizerResult

    labels = {"PERS": "PERSON", "LOC": "LOCATION"}

    class CamelNerRecognizer(EntityRecognizer):
        """CAMeL Tools' Arabic BERT NER as a Presidio recognizer (BIO tags to spans)."""

        def __init__(self) -> None:
            super().__init__(
                supported_entities=list(_KINDS), name="camel_ner", supported_language="ar"
            )

        def load(self) -> None:
            self._ner = NERecognizer(model, use_gpu=False)

        def analyze(self, text: str, entities: list[str], nlp_artifacts: Any = None) -> list:
            words, offsets = [], []
            position = 0
            for word in simple_word_tokenize(text):
                start = text.find(word, position)
                if start < 0:
                    continue
                words.append(word)
                offsets.append((start, start + len(word)))
                position = start + len(word)
            tags: list[str] = []
            for i in range(0, len(words), CHUNK_TOKENS):
                tags.extend(self._ner.predict_sentence(words[i : i + CHUNK_TOKENS]))
            results: list[Any] = []
            current: list[Any] | None = None
            for (start, end), tag in zip(offsets, tags, strict=True):
                prefix, _, label = tag.partition("-")
                entity = labels.get(label)
                if prefix == "I" and current is not None and current[0] == entity:
                    current[2] = end
                    continue
                current = [entity, start, end] if entity in entities else None
                if current is not None:
                    results.append(current)
            return [RecognizerResult(e, s, end, 0.85) for e, s, end in results]

    return CamelNerRecognizer()  # Presidio loads the model in __init__
