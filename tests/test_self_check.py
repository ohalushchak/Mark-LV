"""Stage 3 of self-repair: probes, the stored results, the JUnit reader and the
self_check tool."""
import time
from types import SimpleNamespace

from actions import self_check as sc
from core import capabilities, health


class _Reg:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


def _rec(name, valid=True, error="", probe=None):
    return SimpleNamespace(name=name, valid=valid, error=error, probe=probe)


def test_probes_cover_tools_rejects_and_the_environment(monkeypatch):
    monkeypatch.setattr(capabilities, "ENVIRONMENT", [("python", lambda: (True, "3.12"))])
    monkeypatch.setattr(capabilities, "PROBE_TIMEOUT", 1.0)

    def slow():
        time.sleep(3)
        return True, ""

    capabilities.bind(actions=_Reg([
        _rec("good", probe=lambda: (True, "ready")),
        _rec("broken", probe=lambda: 1 / 0),
        _rec("rejected", valid=False, error="TOOL['handler'] missing"),
        _rec("slow", probe=slow),
        _rec("plain"),                                       # no probe: not listed
    ]))
    try:
        res = capabilities.run_probes()
    finally:
        capabilities.bind()
    by = {i["name"]: i for i in res["items"]}
    assert by["good"]["ok"] and by["python"]["ok"]
    assert not by["broken"]["ok"] and "ZeroDivisionError" in by["broken"]["detail"]
    assert "did not load" in by["rejected"]["detail"]
    assert "no answer within" in by["slow"]["detail"]
    assert "plain" not in by
    assert capabilities.last()["probes"]["items"] == res["items"]
    assert {e["tool"] for e in health.recent()} >= {"broken", "rejected", "slow"}
    assert "3 with a problem" in capabilities.describe(res).replace("4 fine, ", "")


def test_junit_report_is_read_from_the_file(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        "<testcase classname='tests.test_a' name='test_ok'/>"
        "<testcase classname='tests.test_a' name='test_bad'><failure/></testcase>"
        "<testcase classname='tests.test_b' name='test_skip'><skipped/></testcase>"
        "<testcase classname='' name='tests.test_c'><error message='collection'/></testcase>"
        "</testsuite></testsuites>", encoding="utf-8")
    r = capabilities.parse_junit(xml)
    assert r["passed"] == 1 and r["skipped"] == 1 and r["total"] == 4
    assert r["failed"] == ["::tests.test_c", "tests.test_a::test_bad"] and not r["ok"]
    assert capabilities.parse_junit(tmp_path / "none.xml")["error"] == "pytest wrote no report"


def test_tests_run_without_secrets_in_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("GH_TOKEN", "y")
    env = capabilities.test_env()
    assert "GEMINI_API_KEY" not in env and "GH_TOKEN" not in env
    assert env["QT_QPA_PLATFORM"] == "offscreen"


def test_self_check_status_errors_and_detail():
    assert "No tool has failed" in sc.self_check({})
    try:
        {}["missing"]
    except KeyError as e:
        health.record("weather_report", False, "crash", "KeyError: 'missing'", exc=e)
    status = sc.self_check({"action": "status"})
    assert "weather_report" in status and "Latest: [" in status
    listing = sc.self_check({"action": "errors"})
    eid = health.recent()[0]["id"]
    assert f"[{eid}]" in listing
    detail = sc.self_check({"action": "error", "error_id": eid})
    assert "Traceback" in detail and "tests/test_self_check.py" in detail
    assert "No error with id" in sc.self_check({"action": "error", "error_id": "zzzz"})


def test_self_check_probe_reports_and_logs(monkeypatch):
    monkeypatch.setattr(capabilities, "ENVIRONMENT", [("disk", lambda: (False, "0.2 GB free"))])
    capabilities.bind()
    logs = []
    out = sc.self_check({"action": "probe"}, player=SimpleNamespace(write_log=logs.append))
    assert "BAD environment disk: 0.2 GB free" in out
    assert logs[-1] == "SYS: Self-check done — 1 problem(s)."


def test_the_real_tools_declare_working_probes():
    from actions import git_project, project_workspace, run_command
    for mod in (git_project, project_workspace, run_command):
        ok, detail = mod.TOOL["probe"]()
        assert isinstance(ok, bool) and detail


def test_missing_packages_come_with_a_command_that_works_anywhere(monkeypatch, tmp_path):
    (tmp_path / "requirements.txt").write_text("surely-not-installed-pkg>=1\n", encoding="utf-8")
    monkeypatch.setattr(capabilities, "REPO", tmp_path)
    ok, detail = capabilities._check_requirements()
    assert not ok and "python -m pip install -U surely-not-installed-pkg" in detail
    assert "setup.py" not in detail



def test_console_output_is_copied_to_the_log(tmp_path, monkeypatch):
    import sys
    from core import console_log
    monkeypatch.setattr(console_log, "DIR", tmp_path)
    monkeypatch.setattr(console_log, "FILE", tmp_path / "console.log")
    (tmp_path / "console.log").write_text("previous run\n", encoding="utf-8")
    out, err = sys.stdout, sys.stderr
    try:
        assert console_log.install() == tmp_path / "console.log"
        print("[JARVIS] hello 💬")
        sys.stderr.write("Traceback: boom\n")
    finally:
        sys.stdout, sys.stderr = out, err
    text = (tmp_path / "console.log").read_text(encoding="utf-8")
    assert "=== JARVIS started" in text and "hello 💬" in text and "Traceback: boom" in text
    assert (tmp_path / "console.log.1").read_text(encoding="utf-8") == "previous run\n"
