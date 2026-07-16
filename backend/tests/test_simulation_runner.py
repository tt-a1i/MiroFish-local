from app.services import simulation_runner


class _CompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_probe_simulation_environment_success(monkeypatch):
    monkeypatch.setattr(simulation_runner, "_get_simulation_python", lambda: "/tmp/sim-python")
    monkeypatch.setattr(simulation_runner.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(
        simulation_runner.subprocess,
        "run",
        lambda *args, **kwargs: _CompletedProcess(
            returncode=0,
            stdout='{"missing": [], "failures": {}}',
        ),
    )

    status = simulation_runner._probe_simulation_environment()

    assert status["ok"] is True
    assert status["python"] == "/tmp/sim-python"
    assert status["missing_modules"] == []


def test_probe_simulation_environment_reports_missing_modules(monkeypatch):
    monkeypatch.setattr(simulation_runner, "_get_simulation_python", lambda: "/tmp/sim-python")
    monkeypatch.setattr(simulation_runner.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(
        simulation_runner.subprocess,
        "run",
        lambda *args, **kwargs: _CompletedProcess(
            returncode=1,
            stdout='{"missing": ["camel"], "failures": {"camel": "ModuleNotFoundError: No module named \\"camel\\""}}',
        ),
    )

    status = simulation_runner._probe_simulation_environment()

    assert status["ok"] is False
    assert status["missing_modules"] == ["camel"]
    assert "camel" in status["error"]


def test_ensure_simulation_environment_ready_raises_install_hint(monkeypatch):
    monkeypatch.setattr(
        simulation_runner,
        "_probe_simulation_environment",
        lambda python_executable=None: {
            "ok": False,
            "python": "/tmp/sim-python",
            "missing_modules": ["camel", "oasis", "dotenv"],
            "error": "ModuleNotFoundError: No module named 'camel'",
        },
    )

    try:
        simulation_runner._ensure_simulation_environment_ready()
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("预期应抛出 ValueError")

    assert "setup_simulation_env.sh" in message
    assert "camel, oasis, dotenv" in message
