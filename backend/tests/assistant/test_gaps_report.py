"""The gaps & enhancements report (spec story 71): a Markdown file the assistant saves under a
Source System's Outputs. Text is redacted before saving; regenerating shows a diff first and
overwrites only once the user has agreed."""

from __future__ import annotations

from typing import Any

from dawam.modules.llm import Reply

from .test_chat import add_system, ask, conversation, tool

REPORT = "# Gaps\n\n- `orders` has no primary key.\n"


def files(roles, system: str) -> list[dict[str, Any]]:
    response = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/systems/{system}/files"
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def content(roles, file: dict[str, Any]) -> str:
    response = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/files/{file['id']}/content"
    )
    assert response.status_code == 200, response.text
    return response.json()["content"]


def run(roles, fake_llm, system: str, **arguments: Any) -> str:
    """Ask for one ``generate_file`` call; the tool result the model was shown."""
    call = tool("generate_file", system_id=system, name="gaps-report.md", **arguments)
    fake_llm.script(Reply(tool_calls=(call,)), Reply(text="done"))
    owner = roles.client("owner")
    ask(owner, roles, conversation(owner, roles), "Write the gaps report")
    return fake_llm.calls[-1].messages[-1].content or ""


def test_the_report_is_saved_as_a_generated_markdown_output(roles, model, fake_llm):
    system = add_system(roles)

    run(roles, fake_llm, system, content=REPORT)

    [saved] = files(roles, system)
    assert (saved["name"], saved["kind"]) == ("gaps-report.md", "generated")
    assert content(roles, saved) == REPORT


def test_text_is_redacted_with_the_validators_before_saving(roles, model, fake_llm):
    system = add_system(roles)

    run(roles, fake_llm, system, content="Contact ali.hassan@example.com about gaps.\n")

    [saved] = files(roles, system)
    text = content(roles, saved)
    assert "ali.hassan@example.com" not in text and "[redacted: " in text


def test_regenerating_shows_a_diff_and_leaves_the_report_until_confirmed(roles, model, fake_llm):
    system = add_system(roles)
    run(roles, fake_llm, system, content=REPORT)

    shown = run(roles, fake_llm, system, content=REPORT + "- `items` is undocumented.\n")

    assert "needs_confirmation" in shown and "+- `items` is undocumented." in shown
    [saved] = files(roles, system)
    assert content(roles, saved) == REPORT


def test_confirmed_regeneration_overwrites_the_report(roles, model, fake_llm):
    system = add_system(roles)
    run(roles, fake_llm, system, content=REPORT)
    newer = REPORT + "- `items` is undocumented.\n"

    run(roles, fake_llm, system, content=newer, replace=True)

    [saved] = files(roles, system)
    assert content(roles, saved) == newer


def test_identical_regeneration_changes_nothing(roles, model, fake_llm):
    system = add_system(roles)
    run(roles, fake_llm, system, content=REPORT)

    shown = run(roles, fake_llm, system, content=REPORT)

    assert "unchanged" in shown


def test_a_path_in_the_name_still_finds_the_existing_report(roles, model, fake_llm):
    system = add_system(roles)
    run(roles, fake_llm, system, content=REPORT)
    call = tool("generate_file", system_id=system, name="Outputs/gaps-report.md", content="new\n")
    fake_llm.script(Reply(tool_calls=(call,)), Reply(text="done"))
    owner = roles.client("owner")

    ask(owner, roles, conversation(owner, roles), "Again")

    assert "needs_confirmation" in (fake_llm.calls[-1].messages[-1].content or "")
    [saved] = files(roles, system)
    assert content(roles, saved) == REPORT


def test_the_existing_report_is_found_behind_a_full_page_of_files(roles, model, fake_llm):
    system = add_system(roles)
    url = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system}/files"
    for i in range(55):
        sent = roles.client("owner").post(url, files={"file": (f"a{i:02}.md", b"x", "text/plain")})
        assert sent.status_code in (200, 201), sent.text
    run(roles, fake_llm, system, content=REPORT)

    shown = run(roles, fake_llm, system, content="changed\n")

    assert "needs_confirmation" in shown
