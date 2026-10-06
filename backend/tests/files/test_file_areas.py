"""File areas: the Data Warehouse's, in-browser editing and zip downloads (spec §6.17,
stories 163, 164, 165)."""

from __future__ import annotations

import io
import zipfile

from tests.files.test_files import PNG, error_code, stored, system, upload, xlsx_bytes
from tests.roles import RoleClients


def base(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}"


def set_up_warehouse(roles: RoleClients) -> None:
    response = roles.client("owner").post(
        f"{base(roles)}/data-warehouse", json={"target_platform": "postgresql"}
    )
    assert response.status_code == 201, response.text


def dw_upload(roles: RoleClients, name: str, data: bytes, *, as_role="editor"):
    return roles.client(as_role).post(
        f"{base(roles)}/data-warehouse/files", files={"file": (name, data, "text/plain")}
    )


def test_the_data_warehouse_file_area_lists_and_takes_uploads(roles: RoleClients):
    set_up_warehouse(roles)

    created = dw_upload(roles, "ddl.sql", b"CREATE TABLE a (id int);")

    assert created.status_code == 201, created.text
    assert created.json()["owner_kind"] == "data_warehouse"
    assert created.json()["mime"] == "application/sql"
    listed = roles.client("viewer").get(f"{base(roles)}/data-warehouse/files")
    assert [f["name"] for f in listed.json()["items"]] == ["ddl.sql"]
    assert dw_upload(roles, "x.sql", b"select 1", as_role="viewer").status_code == 403


def test_the_data_warehouse_file_area_needs_a_set_up_warehouse(roles: RoleClients):
    response = roles.client("viewer").get(f"{base(roles)}/data-warehouse/files")

    assert response.status_code == 404


def test_data_files_are_accepted_and_keep_their_text(roles: RoleClients):
    system_id = system(roles)
    for name, data in [
        ("q.sql", b"select 1"),
        ("m.yaml", b"a: 1"),
        ("m.yml", b"a: 1"),
        ("d.csv", b"a,b\n1,2"),
        ("d.json", b'{"a": 1}'),
    ]:
        response = upload(roles, system_id, name, data)
        assert response.status_code == 201, (name, response.text)
        assert response.json()["text_status"] == "extracted"


def test_an_editor_edits_a_text_file_in_place(roles: RoleClients):
    file = upload(roles, system(roles), "notes.md", b"# Old").json()
    url = f"{base(roles)}/files/{file['id']}/content"

    read = roles.client("viewer").get(url)
    assert read.status_code == 200
    assert read.json()["content"] == "# Old"

    saved = roles.client("editor").put(url, json={"content": "# New ✓"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["size"] == len("# New ✓".encode())
    assert saved.json()["updated_by"] == str(roles.user("editor").id)
    assert roles.client("viewer").get(url).json()["content"] == "# New ✓"
    text, status, _ = stored(roles.app, file["id"])
    assert text == "# New ✓"
    assert status == "extracted"
    listed = roles.client("viewer").get(f"{base(roles)}/systems/{file['owner_id']}/files")
    assert len(listed.json()["items"]) == 1


def test_a_viewer_cannot_edit_a_file(roles: RoleClients):
    file = upload(roles, system(roles), "notes.md", b"# Old").json()

    response = roles.client("viewer").put(
        f"{base(roles)}/files/{file['id']}/content", json={"content": "x"}
    )

    assert response.status_code == 403


def test_a_binary_file_cannot_be_edited_as_text(roles: RoleClients):
    file = upload(roles, system(roles), "erd.png", PNG).json()
    url = f"{base(roles)}/files/{file['id']}/content"

    assert roles.client("viewer").get(url).status_code == 415
    response = roles.client("editor").put(url, json={"content": "x"})
    assert response.status_code == 415
    assert error_code(response) == "not_editable"


def test_edited_text_that_is_too_large_is_refused(roles: RoleClients):
    file = upload(roles, system(roles), "notes.md", b"# Old").json()

    response = roles.client("editor").put(
        f"{base(roles)}/files/{file['id']}/content",
        json={"content": "x" * (roles.app.state.settings.upload_max_bytes + 1)},
    )

    assert response.status_code == 413


def test_a_json_file_must_stay_text_but_is_not_validated(roles: RoleClients):
    file = upload(roles, system(roles), "d.json", b"{}").json()

    response = roles.client("editor").put(
        f"{base(roles)}/files/{file['id']}/content", json={"content": "{ draft"}
    )

    assert response.status_code == 200


def test_a_binary_file_is_replaced_in_place(roles: RoleClients):
    system_id = system(roles)
    file = upload(roles, system_id, "codes.xlsx", xlsx_bytes()).json()
    url = f"{base(roles)}/files/{file['id']}/replace"

    ok = roles.client("editor").put(url, files={"file": ("whatever.xlsx", xlsx_bytes(), "x/y")})

    assert ok.status_code == 200, ok.text
    assert ok.json()["id"] == file["id"]
    assert ok.json()["name"] == "codes.xlsx"
    bad = roles.client("editor").put(url, files={"file": ("codes.xlsx", b"nope", "x/y")})
    assert bad.status_code == 415
    denied = roles.client("viewer").put(url, files={"file": ("codes.xlsx", xlsx_bytes(), "x/y")})
    assert denied.status_code == 403


def zip_of(response) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_any_member_downloads_a_whole_area_as_a_zip(roles: RoleClients):
    system_id = system(roles)
    upload(roles, system_id, "a.md", b"# A")
    upload(roles, system_id, "b.png", PNG)
    url = f"{base(roles)}/systems/{system_id}/files/download"

    response = roles.client("viewer").get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert zip_of(response) == {"a.md": b"# A", "b.png": PNG}
    assert roles.client("non_member").get(url).status_code == 404


def test_the_data_warehouse_area_downloads_as_a_zip(roles: RoleClients):
    set_up_warehouse(roles)
    dw_upload(roles, "ddl.sql", b"select 1")

    response = roles.client("viewer").get(f"{base(roles)}/data-warehouse/files/download")

    assert response.status_code == 200
    assert zip_of(response) == {"ddl.sql": b"select 1"}
