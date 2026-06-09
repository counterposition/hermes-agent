"""Tests for docker container_config key propagation in execute_code."""

import threading
from unittest.mock import MagicMock, patch

import tools.code_execution_tool as code_execution_tool


def _make_env_config(**overrides):
    base = {
        "env_type": "docker",
        "docker_image": "test-image:latest",
        "singularity_image": "docker://test",
        "modal_image": "test",
        "daytona_image": "test",
        "cwd": "/workspace",
        "host_cwd": "/host/repo",
        "timeout": 180,
        "container_cpu": 2,
        "container_memory": 4096,
        "container_disk": 20480,
        "container_persistent": False,
        "docker_volumes": ["/host/cache:/cache"],
        "docker_mount_cwd_to_workspace": True,
        "docker_forward_env": ["MY_SECRET", "API_KEY"],
        "docker_env": {"NODE_ENV": "test"},
        "docker_run_as_host_user": True,
        "docker_extra_args": ["--cap-drop=ALL"],
        "docker_network": False,
        "docker_persist_across_processes": False,
        "docker_orphan_reaper": False,
        "modal_mode": "direct",
    }
    base.update(overrides)
    return base


def _capture_create_env(env_config, task_id="t1"):
    captured = {}
    mock_env = MagicMock()

    def fake_create_env(**kwargs):
        captured.update(kwargs)
        return mock_env

    with patch("tools.terminal_tool._get_env_config", return_value=env_config), \
         patch("tools.terminal_tool._task_env_overrides", {}), \
         patch("tools.terminal_tool._active_environments", {}), \
         patch("tools.terminal_tool._last_activity", {}), \
         patch("tools.terminal_tool._creation_locks", {}), \
         patch("tools.terminal_tool._creation_locks_lock", threading.Lock()), \
         patch("tools.terminal_tool._env_lock", threading.Lock()), \
         patch("tools.terminal_tool._resolve_container_task_id", side_effect=lambda tid: tid), \
         patch("tools.terminal_tool._create_environment", side_effect=fake_create_env), \
         patch("tools.terminal_tool._start_cleanup_thread"):
        env, env_type = code_execution_tool._get_or_create_env(task_id)

    assert env is mock_env
    assert env_type == "docker"
    return captured


def test_execute_code_forwards_docker_workspace_mount_and_env_config():
    """execute_code-first Docker sessions must use the same Docker config as terminal/file tools."""
    captured = _capture_create_env(_make_env_config())
    cc = captured["container_config"]

    assert captured["host_cwd"] == "/host/repo"
    assert cc["docker_volumes"] == ["/host/cache:/cache"]
    assert cc["docker_mount_cwd_to_workspace"] is True
    assert cc["docker_forward_env"] == ["MY_SECRET", "API_KEY"]
    assert cc["docker_env"] == {"NODE_ENV": "test"}
    assert cc["docker_run_as_host_user"] is True
    assert cc["docker_extra_args"] == ["--cap-drop=ALL"]
    assert cc["docker_network"] is False
    assert cc["docker_persist_across_processes"] is False
    assert cc["docker_orphan_reaper"] is False
    assert cc["modal_mode"] == "direct"


def test_execute_code_uses_canonical_terminal_container_projection():
    """Every setting must match the terminal-first environment path exactly."""
    from tools.terminal_tool import _build_container_config

    cfg = _make_env_config()
    actual = _capture_create_env(cfg, task_id="parity")["container_config"]

    assert actual == _build_container_config(cfg, "docker")


def test_execute_code_docker_config_defaults_when_keys_absent():
    """Missing optional Docker keys keep safe defaults."""
    cfg = _make_env_config()
    del cfg["docker_mount_cwd_to_workspace"]
    del cfg["docker_forward_env"]
    del cfg["docker_env"]
    del cfg["docker_run_as_host_user"]

    cc = _capture_create_env(cfg, task_id="t2")["container_config"]

    assert cc["docker_mount_cwd_to_workspace"] is False
    assert cc["docker_forward_env"] == []
    assert cc["docker_env"] == {}
    assert cc["docker_run_as_host_user"] is False
