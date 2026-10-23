"""The two "no real model in CI" guards have their own tests."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from tools.check_workflow_keys import find_violations, main

from tests.no_real_model import RealModelCallError

REPO_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _workflow(tmp_path: Path, body: str) -> Path:
    (tmp_path / "w.yml").write_text(body, encoding="utf-8")
    return tmp_path


def test_workflow_with_provider_key_fails(tmp_path: Path) -> None:
    root = _workflow(tmp_path, "jobs:\n  t:\n    env:\n      ANTHROPIC_API_KEY: ${{ secrets.K }}\n")
    assert len(find_violations(root)) == 1
    assert main(["x", str(root)]) == 1


def test_workflow_without_key_passes(tmp_path: Path) -> None:
    root = _workflow(tmp_path, "# OPENAI_API_KEY is never set\njobs:\n  t:\n    env:\n      A: b\n")
    assert find_violations(root) == []


def test_repo_workflows_have_no_provider_key() -> None:
    assert REPO_WORKFLOWS.is_dir()
    assert find_violations(REPO_WORKFLOWS) == []


@pytest.mark.parametrize(
    "host",
    [
        "api.anthropic.com",
        "api.openai.com",
        "myres.openai.azure.com",
        "generativelanguage.googleapis.com",
        "bedrock-runtime.us-east-1.amazonaws.com",
    ],
)
def test_real_provider_endpoint_is_refused(host: str) -> None:
    with pytest.raises(RealModelCallError):
        socket.create_connection((host, 443), timeout=1)


def test_localhost_stays_allowed() -> None:
    assert socket.getaddrinfo("localhost", 11434)
