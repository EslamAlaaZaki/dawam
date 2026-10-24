"""Image descriptions (#426): an uploaded image is described by the Workspace's vision model on
indexing, and the description becomes searchable text linked to the image. The same rule as
scanned pages (#425) says when the image may reach the model; if it may not, the image is not
described and the file's ``index_note`` says so. Driven through the jobs queue; the vision model
is the scripted fake provider, so no real model is called."""

from __future__ import annotations

from dawam.modules.files.search import IMAGE_NOT_DESCRIBED, IMAGE_VISION_FAILED
from dawam.modules.llm import LlmError, Reply
from tests.files import test_indexing, test_vision_ocr
from tests.files.test_files import PNG, system, upload
from tests.files.test_indexing import listed, search
from tests.files.test_vision_ocr import use_vision, vision_calls
from tests.roles import RoleClients

worker = test_indexing.worker
policy = test_vision_ocr.policy

ERD = "Table `orders` has a foreign key `customer_id` referencing `customers.id`."


def test_an_erds_description_is_found_by_search(roles: RoleClients, fake_llm, worker):
    use_vision(roles, internal=True)
    before = len(vision_calls(fake_llm))
    fake_llm.script(Reply(text=ERD))
    system_id = system(roles)
    upload(roles, system_id, "erd.png", PNG)

    assert worker() == 1

    done = listed(roles, system_id)["erd.png"]
    assert done["index_status"] == "indexed" and done["text_status"] == "extracted"
    assert done["index_note"] is None
    [call] = vision_calls(fake_llm)[before:]
    assert call.messages[0].images[0].media_type == "image/png"
    assert [p["document"] for p in search(roles, "customer_id")] == ["erd.png"]


def test_an_external_model_describes_it_when_documents_may_be_shared(
    roles: RoleClients,
    fake_llm,
    policy,
    worker,
):
    policy.includes_documents = True
    use_vision(roles, internal=False)
    fake_llm.script(Reply(text=ERD))
    system_id = system(roles)
    upload(roles, system_id, "erd.png", PNG)
    worker()

    assert [p["document"] for p in search(roles, "orders")] == ["erd.png"]


def test_when_the_image_may_not_reach_the_model_it_is_not_described_and_says_so(
    roles: RoleClients, fake_llm, worker
):
    use_vision(roles, internal=False)
    before = len(vision_calls(fake_llm))
    system_id = system(roles)
    upload(roles, system_id, "erd.png", PNG)
    worker()

    done = listed(roles, system_id)["erd.png"]
    assert done["index_status"] == "indexed" and done["text_status"] == "none"
    assert done["index_note"] == IMAGE_NOT_DESCRIBED
    assert vision_calls(fake_llm)[before:] == []


def test_an_image_is_not_described_when_the_workspace_has_no_vision_model(
    roles: RoleClients, fake_llm, worker
):
    system_id = system(roles)
    upload(roles, system_id, "erd.png", PNG)
    worker()

    done = listed(roles, system_id)["erd.png"]
    assert done["index_status"] == "indexed" and done["text_status"] == "none"
    assert done["index_note"] == IMAGE_NOT_DESCRIBED


def test_a_failing_vision_model_leaves_the_image_indexed_without_a_description(
    roles: RoleClients, fake_llm, worker
):
    use_vision(roles, internal=True)
    fake_llm.script(LlmError("bad_request", "The model refused the image."))
    system_id = system(roles)
    upload(roles, system_id, "erd.png", PNG)
    worker()

    done = listed(roles, system_id)["erd.png"]
    assert done["index_status"] == "indexed" and done["text_status"] == "none"
    assert done["index_note"] == IMAGE_VISION_FAILED.format(reason="The model refused the image.")
