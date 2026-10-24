"""CI guard: no provider API key in any GitHub workflow.

No real model is ever called in CI, so a workflow must not set (or read from secrets) an
API key env var of a model provider. Usage: ``python tools/check_workflow_keys.py [DIR]``
(default: ``../.github/workflows``). Exits 1 and prints one line per hit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Matches the env var names providers' SDKs read, as a whole word.
PROVIDER_KEY = re.compile(
    r"\b(?:ANTHROPIC_(?:API_KEY|AUTH_TOKEN)|OPENAI_API_KEY|AZURE_OPENAI_(?:API_)?KEY"
    r"|GEMINI_API_KEY|GOOGLE_(?:GENERATIVE_AI_)?API_KEY|MISTRAL_API_KEY|COHERE_API_KEY"
    r"|GROQ_API_KEY|XAI_API_KEY|DEEPSEEK_API_KEY|OPENROUTER_API_KEY|TOGETHER_API_KEY"
    r"|AWS_BEARER_TOKEN_BEDROCK)\b"
)


def find_violations(workflows_dir: Path) -> list[str]:
    hits: list[str] = []
    files = sorted([*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")])
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if match := PROVIDER_KEY.search(re.sub(r"\s#.*$", "", line)):
                hits.append(f"{path}:{number}: provider API key env var {match.group(0)}")
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("../.github/workflows")
    hits = find_violations(root)
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
