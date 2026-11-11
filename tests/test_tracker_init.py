#!/usr/bin/env python3
"""Integration tests for the GitHub Projects initializer using a stateful fake gh."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INITIALIZER = ROOT / "bin" / "nightshift-tracker-init.sh"
TRACKER_AUTHORITY = ROOT / "bin" / "nightshift_tracker_authority.py"


def _show_tracker(state_root, repository_id):
    """Drive the real `nightshift_tracker_authority.py show` CLI in a
    subprocess -- same posture as `tests/test_tracker_issue_fence.py`'s own
    `run_tracker_cli` -- and return the parsed JSON result."""
    p = subprocess.run(
        [sys.executable, str(TRACKER_AUTHORITY), "show",
         "--state-root", state_root, "--repository-id", str(repository_id)],
        capture_output=True, text=True)
    return json.loads(p.stdout.strip().splitlines()[-1])


FAKE_GH = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_GH_STATE"])
log_path = Path(os.environ["FAKE_GH_LOG"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with log_path.open("a") as stream:
    stream.write(json.dumps(args) + "\n")

def save():
    state_path.write_text(json.dumps(state))

def value(flag):
    return args[args.index(flag) + 1]

def die(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)

def listed_projects():
    if "projects" in state:
        return state["projects"]
    if not state.get("project_exists", True):
        return []
    return [{"id": state["project_id"], "number": 1,
             "title": state["project_title"], "closed": False}]

if args[:2] == ["auth", "status"]:
    print("github.com\n  Token scopes: 'project', 'repo'")
elif args[:2] == ["repo", "view"]:
    if value("--json") == "owner":
        print(state["owner"])
    else:
        print(state["repo"])
elif args[:2] == ["label", "create"]:
    pass
elif args[:2] == ["project", "view"]:
    number = int(args[2])
    matches = [item for item in listed_projects() if item["number"] == number]
    if len(matches) != 1:
        die("unknown project number")
    project = matches[0]
    state["active_project_number"] = number
    save()
    if "--jq" in args:
        query = value("--jq")
        if query == ".id":
            print(project["id"])
        elif query == ".title":
            print(project["title"])
        else:
            die("unexpected project view jq: " + query)
    else:
        print(json.dumps({"id": project["id"], "number": number,
                          "title": project["title"]}))
elif args[:2] == ["project", "list"]:
    if "--limit" not in args or "--closed" not in args or "--format" not in args:
        die("project discovery must request a complete JSON list")
    limit = int(value("--limit"))
    projects = listed_projects()
    print(json.dumps({"projects": projects[:limit],
                      "totalCount": state.get("project_total_count", len(projects))}))
elif args[:2] == ["project", "create"]:
    expected = "Delivery — " + state["repo"]
    if value("--title") != expected:
        die("wrong new project title")
    state["project_exists"] = True
    state["project_title"] = expected
    state["active_project_number"] = 1
    state["project_create_calls"] = state.get("project_create_calls", 0) + 1
    save()
    print("1")
elif args[:2] == ["project", "edit"]:
    expected = "Delivery — " + state["repo"]
    if value("--title") != expected:
        die("wrong project title")
    if state.get("fail_project_edit"):
        die("injected project edit failure", 13)
    state["project_title"] = expected
    if "projects" in state:
        number = int(args[2])
        match = next(item for item in state["projects"] if item["number"] == number)
        match["title"] = expected
    state["project_edit_calls"] = state.get("project_edit_calls", 0) + 1
    save()
elif args[:2] == ["project", "link"]:
    if state.get("fail_link"):
        die("project link failed", 9)
elif args[:2] == ["project", "field-list"]:
    if "--jq" in args:
        print("\n".join(field["name"] for field in state["fields"]))
    else:
        print(json.dumps({"fields": state["fields"]}))
elif args[:2] == ["project", "field-create"]:
    name = value("--name")
    if any(field["name"] == name for field in state["fields"]):
        die("duplicate field")
    data_type = value("--data-type")
    database_id = max(field["databaseId"] for field in state["fields"]) + 1
    field = {"id": f"F_{database_id}", "databaseId": database_id,
             "name": name, "dataType": data_type}
    if "--single-select-options" in args:
        field["options"] = value("--single-select-options").split(",")
    state["fields"].append(field)
    save()
elif args[:2] == ["issue", "list"] and "--search" in args:
    if "status_issues" in state:
        import subprocess
        rows = [row for row in state["status_issues"]
                if row["author"]["login"] == value("--author")]
        print(subprocess.run(["jq", "-r", value("--jq")], input=json.dumps(rows),
                             capture_output=True, text=True, check=True).stdout, end="")
    else:
        print(str(state.get("status_issue", 99)))
elif args[:2] == ["api", "user"]:
    if value("--jq") != ".login":
        die("unexpected api user jq")
    print(state.get("me", "acme"))
elif args[:2] == ["issue", "create"]:
    state["created_issue"] = {"title": value("--title"), "repo": value("--repo")}
    save()
    print("https://github.com/acme/product/issues/123")
elif args[:2] == ["issue", "pin"]:
    pass
elif args[:2] == ["api", "users/acme"]:
    print(json.dumps({"type": state.get("owner_type", "User"),
                      "id": state.get("owner_id", 42)}))
elif args[:2] == ["api", "graphql"]:
    query = value("-f")
    if "deleteProjectV2View" in query:
        die("delete must never be called", 10)
    if "updateProjectV2View" in query:
        if "input: {viewId: $viewId, name: $name}" not in query:
            die("view mutation must contain only viewId and name", 10)
        pairs = [arg for arg in args if "=" in arg and not arg.startswith("query=")]
        if sorted(pair.split("=", 1)[0] for pair in pairs) != ["name", "viewId"]:
            die("unexpected view mutation inputs", 10)
        fields = dict(pair.split("=", 1) for pair in pairs)
        if fields["name"] != "All work":
            die("wrong default view title", 10)
        matches = [view for view in state["views"] if view["id"] == fields["viewId"]]
        if len(matches) != 1:
            die("unknown view id", 10)
        if state.get("fail_view_rename"):
            die("injected view rename failure", 14)
        matches[0]["name"] = "All work"
        if state.get("drift_after_view_rename"):
            matches[0]["filter"] = "is:pr"
        state["view_rename_calls"] = state.get("view_rename_calls", 0) + 1
        save()
        print(json.dumps({"data": {"updateProjectV2View": {
            "projectV2View": {"id": matches[0]["id"],
                              "number": matches[0]["number"],
                              "name": matches[0]["name"]}
        }}}))
        sys.exit(0)
    if "mutation" in query:
        die("unexpected mutation", 10)
    if "views(first: 100)" not in query:
        die("unexpected graphql query")
    print(json.dumps({"data": {"node": {
        "views": {"pageInfo": {"hasNextPage": state.get("views_has_next_page", False)},
                  "nodes": state["views"]},
        "fields": {"nodes": state["fields"]},
    }}}))
elif args and args[0] == "api" and "--method" in args and value("--method") == "POST":
    endpoint = next(arg for arg in args if "projectsV2/" in arg and arg.endswith("/views"))
    project_number = state.get("active_project_number", 1)
    expected_endpoint = (
        f"orgs/acme/projectsV2/{project_number}/views"
        if state.get("owner_type") == "Organization"
        else f"users/acme/projectsV2/{project_number}/views"
    )
    if endpoint != expected_endpoint:
        die("wrong project views endpoint: " + endpoint, 12)
    pairs = [arg for arg in args if arg.startswith(("name=", "layout=", "filter="))]
    params = dict(pair.split("=", 1) for pair in pairs)
    if state.get("fail_post") == params["name"]:
        die("injected view POST failure", 11)
    visible = [int(arg.split("=", 1)[1]) for arg in args
               if arg.startswith("visible_fields[]=")]
    horizontal = [int(arg.split("=", 1)[1]) for arg in args
                  if arg.startswith("group_by[]=")]
    vertical = [int(arg.split("=", 1)[1]) for arg in args
                if arg.startswith("vertical_group_by[]=")]
    by_database_id = {field["databaseId"]: field for field in state["fields"]}
    view = {
        "id": f"VIEW_{len(state['views']) + 1}",
        "number": len(state["views"]) + 1,
        "name": params["name"],
        "layout": params["layout"].upper() + "_LAYOUT",
        "filter": params["filter"],
        "fields": {"nodes": [by_database_id[item] for item in visible]},
        "groupByFields": {"nodes": [by_database_id[item] for item in horizontal]},
        "verticalGroupByFields": {"nodes": [by_database_id[item] for item in vertical]},
    }
    state["views"].append(view)
    state.setdefault("post_endpoints", []).append(endpoint)
    save()
    print(json.dumps(view))
else:
    die("unexpected gh invocation: " + " ".join(args))
'''


def field(database_id: int, name: str, data_type: str = "TEXT") -> dict:
    return {
        "id": f"F_{database_id}",
        "databaseId": database_id,
        "name": name,
        "dataType": data_type,
    }


def empty_view(number: int, name: str = "View 1") -> dict:
    return {
        "id": f"VIEW_{number}",
        "number": number,
        "name": name,
        "layout": "TABLE_LAYOUT",
        "filter": "",
        "fields": {"nodes": []},
        "groupByFields": {"nodes": []},
        "verticalGroupByFields": {"nodes": []},
    }


class TrackerInitTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        tmp_path = Path(temporary.name)
        self.repo = tmp_path / "repo path"
        (self.repo / ".omni").mkdir(parents=True)
        (self.repo / ".omni" / "tracker.json").write_text(json.dumps({
            "provider": "github",
            "owner": "acme",
            "repo": "product",
            "project_number": 1,
            "project_title": "product — delivery",
        }))
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        gh = fake_bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.state_path = tmp_path / "state.json"
        self.log_path = tmp_path / "gh.log"
        self.log_path.write_text("")
        self.write_state({
            "owner": "acme",
            "repo": "product",
            "project_id": "PVT_PROJECT",
            "project_title": "product — delivery",
            "fields": [
                field(101, "Title", "TITLE"),
                field(102, "Delivery stage", "SINGLE_SELECT"),
                field(103, "Lane", "SINGLE_SELECT"),
                field(104, "Active roles"),
                field(105, "Current action"),
            ],
            "views": [empty_view(1)],
        })
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{fake_bin}:{self.env['PATH']}",
            "FAKE_GH_STATE": str(self.state_path),
            "FAKE_GH_LOG": str(self.log_path),
        })
        # Deterministic by construction (#698): the tracker-binding step
        # this test class also covers reacts to these three variables, so
        # every test starts with none of them ambient from the real shell
        # this suite happens to run under -- each test that needs one sets
        # it explicitly.
        for name in ("NIGHTSHIFT_STATE_ROOT", "NIGHTSHIFT_REPOSITORY_ID",
                     "NIGHTSHIFT_TRACKER_SLUG"):
            self.env.pop(name, None)

    def run_init(self, *extra, env=None):
        return subprocess.run(
            [str(INITIALIZER), str(self.repo), *extra],
            text=True,
            capture_output=True,
            env=env if env is not None else self.env,
            check=False,
        )

    def read_state(self):
        return json.loads(self.state_path.read_text())

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state))

    def read_log(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def assert_no_project_writes(self):
        calls = self.read_log()
        self.assertFalse(any(
            call[:2] in (["project", "create"], ["project", "link"],
                         ["project", "field-create"], ["project", "edit"])
            for call in calls
        ))
        self.assertFalse(any(
            call and call[0] == "api" and "--method" in call
            for call in calls
        ))
        self.assertFalse(any(
            "updateProjectV2View" in arg for call in calls for arg in call
        ))

    def test_status_issue_reuse_requires_this_identity_and_the_exact_title(self):
        """Audit B1/B3: anyone can open an issue titled "Program status" on a
        public repository, and the adopted one receives every report."""
        state = self.read_state()
        state.update(me="acme", status_issues=[
            {"number": 7, "title": "Program status", "author": {"login": "mallory"}},
            {"number": 8, "title": "Program status (old)", "author": {"login": "acme"}},
        ])
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["created_issue"],
                         {"title": "Program status", "repo": "acme/product"})
        self.assertIn("NIGHTSHIFT_ALERT_ISSUE=123", result.stdout)
        listing = next(call for call in self.read_log() if call[:2] == ["issue", "list"])
        self.assertEqual(listing[listing.index("--author") + 1], "acme")

        state = self.read_state()
        state.pop("created_issue")
        state["status_issues"].append(
            {"number": 9, "title": "Program status", "author": {"login": "acme"}})
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("reusing issue #9", result.stdout)
        self.assertNotIn("created_issue", self.read_state())

    def test_unresolvable_login_refuses_before_choosing_a_status_issue(self):
        state = self.read_state()
        state["me"] = "not a login"
        self.write_state(state)
        result = self.run_init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot resolve the authenticated GitHub login", result.stderr)
        self.assertFalse(any(call[:2] == ["issue", "create"] for call in self.read_log()))

    def test_create_is_additive_and_builds_exact_views(self):
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)

        state = self.read_state()
        self.assertEqual([view["name"] for view in state["views"]], [
            "All work", "Delivery now", "Agent activity"
        ])
        self.assertEqual(state["project_title"], "Delivery — product")
        self.assertEqual(state["project_edit_calls"], 1)
        self.assertEqual(state["view_rename_calls"], 1)
        self.assertEqual(state["views"][0], empty_view(1, "All work"))
        activity = next(item for item in state["fields"] if item["name"] == "Activity")
        self.assertEqual(activity["options"], ["Active", "Idle"])
        by_name = {view["name"]: view for view in state["views"]}
        expected_fields = [
            "Title", "Activity", "Delivery stage", "Lane", "Active roles", "Current action"
        ]
        delivery = by_name["Delivery now"]
        self.assertEqual(delivery["layout"], "BOARD_LAYOUT")
        self.assertEqual(delivery["filter"], "is:issue")
        self.assertEqual(
            [item["name"] for item in delivery["fields"]["nodes"]], expected_fields
        )
        self.assertEqual(delivery["groupByFields"]["nodes"], [])
        self.assertEqual(
            [item["name"] for item in delivery["verticalGroupByFields"]["nodes"]],
            ["Delivery stage"],
        )
        agents = by_name["Agent activity"]
        self.assertEqual(agents["layout"], "TABLE_LAYOUT")
        self.assertEqual(agents["filter"], "activity:Active")
        self.assertEqual(
            [item["name"] for item in agents["fields"]["nodes"]], expected_fields
        )
        self.assertEqual(
            [item["name"] for item in agents["groupByFields"]["nodes"]], ["Lane"]
        )
        self.assertEqual(agents["verticalGroupByFields"]["nodes"], [])
        self.assertEqual(state["post_endpoints"], [
            "users/acme/projectsV2/1/views",
            "users/acme/projectsV2/1/views",
        ])

        calls = self.read_log()
        self.assertFalse(any("deleteProjectV2View" in " ".join(call) for call in calls))
        mutation = next(call for call in calls if any(
            "updateProjectV2View" in arg for arg in call
        ))
        self.assertEqual(
            sorted(arg.split("=", 1)[0] for arg in mutation
                   if "=" in arg and not arg.startswith("query=")),
            ["name", "viewId"],
        )
        rename_index = calls.index(mutation)
        edit_index = next(
            index for index, call in enumerate(calls)
            if call[:2] == ["project", "edit"]
        )
        self.assertLess(rename_index, edit_index)
        self.assertGreaterEqual(sum(
            1 for call in calls[:rename_index]
            if any("views(first: 100)" in arg for arg in call)
        ), 2)
        self.assertFalse(any(
            call[:2] in (["project", "item-list"], ["project", "item-add"],
                         ["project", "item-edit"])
            for call in calls
        ))
        self.assertIn("https://github.com/users/acme/projects/1", result.stdout)
        command_line = next(
            line.strip() for line in result.stdout.splitlines() if line.endswith(" --apply")
        )
        self.assertEqual(shlex.split(command_line), [
            str(ROOT / "bin" / "nightshift-tracker-sync"),
            "--repo", str(self.repo),
            "--db", str(self.repo / ".omni" / "control.db"),
            "--apply",
        ])

    def test_rerun_is_idempotent(self):
        first = self.run_init()
        self.assertEqual(first.returncode, 0, first.stderr)
        posts_before = sum("--method" in call and "POST" in call for call in self.read_log())
        second = self.run_init()
        self.assertEqual(second.returncode, 0, second.stderr)
        posts_after = sum("--method" in call and "POST" in call for call in self.read_log())
        self.assertEqual(posts_after, posts_before)
        self.assertEqual(posts_before, 2)
        state = self.read_state()
        self.assertEqual(state["view_rename_calls"], 1)
        self.assertEqual(state["project_edit_calls"], 1)
        self.assertEqual([view["name"] for view in state["views"]].count("Delivery now"), 1)
        self.assertEqual([view["name"] for view in state["views"]].count("Agent activity"), 1)
        self.assertEqual([item["name"] for item in state["fields"]].count("Activity"), 1)

    def test_rerun_accepts_canonical_visible_field_order(self):
        first = self.run_init()
        self.assertEqual(first.returncode, 0, first.stderr)
        state = self.read_state()
        for view in state["views"]:
            if view["name"] in {"Delivery now", "Agent activity"}:
                view["fields"]["nodes"].sort(key=lambda item: item["databaseId"])
        self.write_state(state)
        posts_before = sum(
            "--method" in call and "POST" in call for call in self.read_log()
        )
        second = self.run_init()
        self.assertEqual(second.returncode, 0, second.stderr)
        posts_after = sum(
            "--method" in call and "POST" in call for call in self.read_log()
        )
        self.assertEqual(posts_after, posts_before)

    def test_visible_field_membership_rejects_missing_extra_and_duplicate_ids(self):
        first = self.run_init()
        self.assertEqual(first.returncode, 0, first.stderr)
        pristine = self.read_state()
        for variant in ("missing", "extra", "duplicate"):
            with self.subTest(variant=variant):
                state = json.loads(json.dumps(pristine))
                view = next(
                    item for item in state["views"] if item["name"] == "Delivery now"
                )
                visible = view["fields"]["nodes"]
                if variant == "missing":
                    visible.pop()
                elif variant == "extra":
                    visible.append(field(999, "Unexpected"))
                else:
                    visible.append(dict(visible[0]))
                self.write_state(state)
                self.log_path.write_text("")
                result = self.run_init()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("different configuration", result.stderr)
                self.assertFalse(any(
                    "--method" in call and "POST" in call for call in self.read_log()
                ))

    def test_view_post_failure_preserves_views_and_rerun_recovers(self):
        state = self.read_state()
        state["fail_post"] = "Agent activity"
        self.write_state(state)
        failed = self.run_init()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(
            [view["name"] for view in self.read_state()["views"]],
            ["View 1", "Delivery now"],
        )

        state = self.read_state()
        state.pop("fail_post")
        self.write_state(state)
        recovered = self.run_init()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            [view["name"] for view in self.read_state()["views"]],
            ["All work", "Delivery now", "Agent activity"],
        )
        self.assertFalse(any(
            "deleteProjectV2View" in " ".join(call) for call in self.read_log()
        ))

    def test_duplicate_desired_view_refuses_before_post(self):
        state = self.read_state()
        state["views"].extend([
            empty_view(2, "Delivery now"), empty_view(3, "Delivery now")
        ])
        self.write_state(state)
        result = self.run_init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 views named 'Delivery now'", result.stderr)
        self.assertFalse(any(
            "--method" in call and "POST" in call for call in self.read_log()
        ))
        self.assertEqual(
            [view["name"] for view in self.read_state()["views"]],
            ["View 1", "Delivery now", "Delivery now"],
        )

    def test_drifted_desired_view_refuses_before_post(self):
        state = self.read_state()
        drifted = empty_view(2, "Delivery now")
        drifted["layout"] = "BOARD_LAYOUT"
        drifted["filter"] = "is:pr"
        state["views"].append(drifted)
        self.write_state(state)
        result = self.run_init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different configuration", result.stderr)
        self.assertFalse(any(
            "--method" in call and "POST" in call for call in self.read_log()
        ))
        self.assertEqual(
            [view["name"] for view in self.read_state()["views"]],
            ["View 1", "Delivery now"],
        )

    def test_project_link_failure_is_not_swallowed(self):
        state = self.read_state()
        state["fail_link"] = True
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 9)
        self.assertFalse(any(
            "--method" in call and "POST" in call for call in self.read_log()
        ))

    def test_organization_uses_organization_project_url(self):
        state = self.read_state()
        state["owner_type"] = "Organization"
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://github.com/orgs/acme/projects/1", result.stdout)
        self.assertEqual(self.read_state()["post_endpoints"], [
            "orgs/acme/projectsV2/1/views", "orgs/acme/projectsV2/1/views"
        ])

    def test_existing_all_work_number_one_is_reused(self):
        state = self.read_state()
        state["views"] = [empty_view(1, "All work")]
        state["project_title"] = "Delivery — product"
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertNotIn("view_rename_calls", state)
        self.assertNotIn("project_edit_calls", state)

    def test_existing_delivery_views_keep_identity_and_configuration(self):
        state = self.read_state()
        activity = field(106, "Activity", "SINGLE_SELECT")
        activity["options"] = ["Active", "Idle"]
        state["fields"].append(activity)
        visible = [state["fields"][index] for index in (0, 5, 1, 2, 3, 4)]
        delivery = {
            "id": "VIEW_DELIVERY", "number": 2, "name": "Delivery now",
            "layout": "BOARD_LAYOUT", "filter": "is:issue",
            "fields": {"nodes": visible}, "groupByFields": {"nodes": []},
            "verticalGroupByFields": {"nodes": [state["fields"][1]]},
        }
        agents = {
            "id": "VIEW_AGENTS", "number": 3, "name": "Agent activity",
            "layout": "TABLE_LAYOUT", "filter": "activity:Active",
            "fields": {"nodes": visible},
            "groupByFields": {"nodes": [state["fields"][2]]},
            "verticalGroupByFields": {"nodes": []},
        }
        state["views"].extend([delivery, agents])
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        views = {view["name"]: view for view in self.read_state()["views"]}
        self.assertEqual(views["Delivery now"], delivery)
        self.assertEqual(views["Agent activity"], agents)

    def test_custom_configured_or_live_title_is_refused_before_naming_writes(self):
        for location in ("config", "live"):
            with self.subTest(location=location):
                state = self.read_state()
                state["project_title"] = "product — delivery"
                self.write_state(state)
                config = json.loads((self.repo / ".omni" / "tracker.json").read_text())
                config["project_title"] = (
                    "My custom board" if location == "config" else "product — delivery"
                )
                (self.repo / ".omni" / "tracker.json").write_text(json.dumps(config))
                if location == "live":
                    state["project_title"] = "My custom board"
                    self.write_state(state)
                self.log_path.write_text("")
                result = self.run_init()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unrecognized", result.stderr)
                calls = self.read_log()
                self.assertFalse(any(call[:2] == ["project", "edit"] for call in calls))
                self.assertFalse(any(
                    "updateProjectV2View" in arg for call in calls for arg in call
                ))
                config["project_title"] = "product — delivery"
                (self.repo / ".omni" / "tracker.json").write_text(json.dumps(config))

    def test_ambiguous_missing_or_nondefault_view_is_refused(self):
        variants = {
            "missing": [],
            "both": [empty_view(1), empty_view(2, "All work")],
            "wrong-number-view-one": [empty_view(2)],
            "wrong-number-all-work": [empty_view(2, "All work")],
        }
        for name, views in variants.items():
            with self.subTest(name=name):
                state = self.read_state()
                state["views"] = views
                self.write_state(state)
                self.log_path.write_text("")
                result = self.run_init()
                self.assertNotEqual(result.returncode, 0)
                calls = self.read_log()
                self.assertFalse(any(call[:2] == ["project", "edit"] for call in calls))
                self.assertFalse(any(
                    "updateProjectV2View" in arg for call in calls for arg in call
                ))

    def test_incomplete_view_page_is_refused(self):
        state = self.read_state()
        state["views_has_next_page"] = True
        self.write_state(state)
        result = self.run_init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("view state is incomplete", result.stderr)
        calls = self.read_log()
        self.assertFalse(any(call[:2] == ["project", "edit"] for call in calls))
        self.assertFalse(any(
            "updateProjectV2View" in arg for call in calls for arg in call
        ))

    def test_future_default_and_tracked_title_are_canonical(self):
        script = INITIALIZER.read_text()
        self.assertIn('PROJECT_TITLE="Delivery — $REPO"', script)
        tracked = json.loads((ROOT / ".omni" / "tracker.json").read_text())
        self.assertEqual(tracked["project_title"], "Delivery — nightshift")

        (self.repo / ".omni" / "tracker.json").unlink()
        state = self.read_state()
        state["project_exists"] = False
        state["project_title"] = ""
        self.write_state(state)
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = json.loads((self.repo / ".omni" / "tracker.json").read_text())
        self.assertEqual(generated["project_title"], "Delivery — product")
        self.assertEqual(self.read_state()["project_create_calls"], 1)

    def test_unconfigured_duplicate_canonical_projects_fail_before_writes(self):
        (self.repo / ".omni" / "tracker.json").unlink()
        state = self.read_state()
        state["projects"] = [
            {"id": "PVT_A", "number": 31, "title": "Delivery — product",
             "closed": False},
            {"id": "PVT_B", "number": 32, "title": "Delivery — product",
             "closed": False},
        ]
        self.write_state(state)

        result = self.run_init()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("found 2 Projects named 'Delivery — product'", result.stderr)
        self.assert_no_project_writes()

    def test_unconfigured_project_beyond_default_page_is_reused(self):
        (self.repo / ".omni" / "tracker.json").unlink()
        state = self.read_state()
        state["project_title"] = "Delivery — product"
        state["projects"] = [
            {"id": f"PVT_OTHER_{number}", "number": number,
             "title": f"Other board {number}", "closed": False}
            for number in range(1, 35)
        ] + [{"id": "PVT_DELIVERY", "number": 35,
              "title": "Delivery — product", "closed": False}]
        self.write_state(state)

        result = self.run_init()

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.read_log()
        discovery = next(call for call in calls if call[:2] == ["project", "list"])
        self.assertGreater(int(discovery[discovery.index("--limit") + 1]), 30)
        self.assertIn("--closed", discovery)
        self.assertFalse(any(call[:2] == ["project", "create"] for call in calls))
        self.assertTrue(any(
            call[:3] == ["project", "link", "35"] for call in calls
        ))
        self.assertEqual(self.read_state()["post_endpoints"], [
            "users/acme/projectsV2/35/views",
            "users/acme/projectsV2/35/views",
        ])
        generated = json.loads((self.repo / ".omni" / "tracker.json").read_text())
        self.assertEqual(generated["project_number"], 35)
        self.assertIn("https://github.com/users/acme/projects/35", result.stdout)

    def test_unconfigured_incomplete_project_list_fails_before_writes(self):
        (self.repo / ".omni" / "tracker.json").unlink()
        state = self.read_state()
        state["projects"] = [
            {"id": "PVT_A", "number": 1, "title": "Other board",
             "closed": False},
        ]
        state["project_total_count"] = 2
        self.write_state(state)

        result = self.run_init()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Project discovery is incomplete (1 of 2)", result.stderr)
        self.assert_no_project_writes()

    def test_unconfigured_unsafe_legacy_or_closed_projects_fail_before_writes(self):
        cases = {
            "closed-canonical": (
                [{"id": "PVT_CANONICAL", "number": 6,
                  "title": "Delivery — product", "closed": True}],
                "Project #6 named 'Delivery — product' is closed",
            ),
            "closed-legacy": (
                [{"id": "PVT_LEGACY", "number": 7,
                  "title": "product — delivery", "closed": True}],
                "legacy Project #7 is closed",
            ),
            "duplicate-legacy": (
                [{"id": "PVT_LEGACY_A", "number": 7,
                  "title": "product — delivery", "closed": False},
                 {"id": "PVT_LEGACY_B", "number": 8,
                  "title": "product — delivery", "closed": False}],
                "found 2 Projects named 'product — delivery'",
            ),
            "unbound-generic": (
                [{"id": "PVT_GENERIC", "number": 9,
                  "title": "Delivery", "closed": False}],
                "an unconfigured Project named 'Delivery' cannot be safely tied",
            ),
        }
        for name, (projects, expected_error) in cases.items():
            with self.subTest(name=name):
                (self.repo / ".omni" / "tracker.json").unlink(missing_ok=True)
                state = self.read_state()
                state["projects"] = projects
                state.pop("project_total_count", None)
                self.write_state(state)
                self.log_path.write_text("")

                result = self.run_init()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                self.assert_no_project_writes()

    def test_unconfigured_unique_repo_legacy_project_is_adopted(self):
        (self.repo / ".omni" / "tracker.json").unlink()
        state = self.read_state()
        state["projects"] = [
            {"id": "PVT_LEGACY", "number": 7,
             "title": "product — delivery", "closed": False},
        ]
        self.write_state(state)

        result = self.run_init()

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.read_log()
        self.assertFalse(any(call[:2] == ["project", "create"] for call in calls))
        self.assertTrue(any(call[:3] == ["project", "link", "7"] for call in calls))
        self.assertEqual(self.read_state()["project_title"], "Delivery — product")
        generated = json.loads((self.repo / ".omni" / "tracker.json").read_text())
        self.assertEqual(generated["project_number"], 7)

    def test_noncanonical_requested_title_is_refused(self):
        result = self.run_init("My custom board")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("project title must be 'Delivery — product'", result.stderr)
        self.assertEqual(self.read_log(), [
            ["auth", "status"], ["auth", "status"],
            ["repo", "view", "--json", "owner", "--jq", ".owner.login"],
            ["repo", "view", "--json", "name", "--jq", ".name"],
        ])

    def test_view_readback_drift_refuses_before_project_title_edit(self):
        state = self.read_state()
        state["drift_after_view_rename"] = True
        self.write_state(state)
        result = self.run_init()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed beyond its name", result.stderr)
        state = self.read_state()
        self.assertEqual(state["project_title"], "product — delivery")
        self.assertNotIn("project_edit_calls", state)

    def test_partial_title_failure_recovers_without_second_view_rename(self):
        state = self.read_state()
        state["fail_project_edit"] = True
        self.write_state(state)
        failed = self.run_init()
        self.assertEqual(failed.returncode, 13)
        state = self.read_state()
        self.assertEqual(state["views"][0]["name"], "All work")
        self.assertEqual(state["project_title"], "product — delivery")
        self.assertEqual(state["view_rename_calls"], 1)

        state.pop("fail_project_edit")
        self.write_state(state)
        recovered = self.run_init()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        state = self.read_state()
        self.assertEqual(state["project_title"], "Delivery — product")
        self.assertEqual(state["view_rename_calls"], 1)
        self.assertEqual(state["project_edit_calls"], 1)

    def test_view_rename_failure_leaves_title_and_rerun_recovers(self):
        state = self.read_state()
        state["fail_view_rename"] = True
        self.write_state(state)
        failed = self.run_init()
        self.assertEqual(failed.returncode, 14)
        state = self.read_state()
        self.assertEqual(state["views"][0]["name"], "View 1")
        self.assertEqual(state["project_title"], "product — delivery")
        self.assertNotIn("project_edit_calls", state)

        state.pop("fail_view_rename")
        self.write_state(state)
        recovered = self.run_init()
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        state = self.read_state()
        self.assertEqual(state["views"][0]["name"], "All work")
        self.assertEqual(state["project_title"], "Delivery — product")

    def test_tracker_binding_skipped_without_explicit_slug_even_with_scope_set(self):
        """ADR-0088: `NIGHTSHIFT_STATE_ROOT`/`NIGHTSHIFT_REPOSITORY_ID` are
        ambient on the runtime host regardless of which repository this
        script is bootstrapping (a customer's own board, a dry run) --
        their presence alone must NOT auto-declare this checkout's
        `$OWNER/$REPO` (here, the fake gh harness's `acme/product`) as
        Nightshift's own tracker. Only the THIRD, separately-named
        `NIGHTSHIFT_TRACKER_SLUG` variable triggers the declare."""
        state_root = tempfile.mkdtemp(prefix="nightshift-tracker-state-")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        env = dict(self.env)
        env["NIGHTSHIFT_STATE_ROOT"] = state_root
        env["NIGHTSHIFT_REPOSITORY_ID"] = "42"
        result = self.run_init(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NIGHTSHIFT_TRACKER_SLUG not set", result.stdout)
        resolved = _show_tracker(state_root, 42)
        self.assertIsNone(resolved.get("tracker"))
        self.assertIn(resolved.get("source"), ("registry_absent",))

    def test_tracker_binding_declares_explicit_slug_not_derived_owner_repo(self):
        """The value actually written is the explicit
        `NIGHTSHIFT_TRACKER_SLUG`, never the cwd/`gh repo view`-derived
        `$OWNER/$REPO` -- proven by setting it to a DIFFERENT repository
        than the fake gh harness's own `acme/product` and asserting THAT
        different value is what gets declared."""
        state_root = tempfile.mkdtemp(prefix="nightshift-tracker-state-")
        self.addCleanup(shutil.rmtree, state_root, ignore_errors=True)
        env = dict(self.env)
        env["NIGHTSHIFT_STATE_ROOT"] = state_root
        env["NIGHTSHIFT_REPOSITORY_ID"] = "42"
        env["NIGHTSHIFT_TRACKER_SLUG"] = "ns-org/ns-tracker"
        result = self.run_init(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("declared tracker: ns-org/ns-tracker", result.stdout)
        resolved = _show_tracker(state_root, 42)
        self.assertEqual(resolved.get("tracker"), "ns-org/ns-tracker")
        self.assertNotEqual(resolved.get("tracker"), "acme/product")


if __name__ == "__main__":
    unittest.main()
