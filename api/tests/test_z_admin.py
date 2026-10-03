"""Runs last (file name): it switches the active tileset of the shared fixture.
Admin writes carry the real user into the audit log, and the DB rules show as friendly errors."""
import pytest
from django.contrib.auth.models import Group, User
from django.db import connections
from django.test import Client


@pytest.fixture
def editor(db_access):
    u, _ = User.objects.get_or_create(username="pytest_editor", defaults={"is_staff": True})
    u.groups.set([Group.objects.get(name="admin")])
    return u


@pytest.fixture(scope="session")
def db_access():
    yield


def audit_users(pk):
    with connections["default"].cursor() as cur:
        cur.execute("SELECT app_user, op FROM audit.change_log WHERE table_name='catalog.tileset' AND pk=%s ORDER BY id", [str(pk)])
        return cur.fetchall()


def test_make_active_switches_build_and_audits_user(data, editor):
    c = Client(); c.force_login(editor)
    r = c.post("/admin/catalog/tileset/", {"action": "make_active", "_selected_action": [str(data["ts2"])]}, follow=True)
    assert r.status_code == 200
    with connections["default"].cursor() as cur:
        cur.execute("SELECT id, is_active, status FROM catalog.tileset WHERE layer_id=%s ORDER BY version", [str(data[1])])
        rows = cur.fetchall()
    assert [(x[1], x[2]) for x in rows] == [(False, "retired"), (True, "ready")]
    assert ("pytest_editor", "UPDATE") in audit_users(data["ts2"])


def test_rule_violation_is_a_friendly_message(data, editor):
    c = Client(); c.force_login(editor)
    # v1 is retired now; reactivating it directly while v2 is active must be refused by the DB, not crash
    r = c.post("/admin/catalog/tileset/", {"action": "make_active", "_selected_action": [str(data["ts1"])]}, follow=True)
    assert r.status_code == 200   # v1 is retired and cannot be active (retired is terminal): message, not a 500
    with connections["default"].cursor() as cur:
        cur.execute("SELECT is_active FROM catalog.tileset WHERE id=%s", [str(data["ts2"])])
        assert cur.fetchone()[0] is True


def test_history_page_renders(data, editor):
    c = Client(); c.force_login(editor)
    r = c.get(f"/admin/catalog/layer/{data[1]}/history/")
    assert r.status_code == 200 and b"pytest" in r.content


def test_viewer_group_is_read_only_in_admin(data):
    u, _ = User.objects.get_or_create(username="pytest_viewer", defaults={"is_staff": True})
    u.groups.set([Group.objects.get(name="viewer")])
    c = Client(); c.force_login(u)
    assert c.get("/admin/catalog/inventory/").status_code == 200
    assert c.post("/admin/catalog/tileset/add/", {}).status_code == 403
