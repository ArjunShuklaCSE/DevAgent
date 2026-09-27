import copy
import json
from typing import Any

import pytest

from sandbox.docker_proxy import (
    ProxySettings,
    Request,
    _list_is_filtered,
    parse_head,
    validate_create,
)

SETTINGS = ProxySettings(
    allowed_images=["devagent-sandbox:dev"],
    allowed_volumes=["devagent-workspaces"],
    allowed_bind_roots=["/srv/workspaces"],
)

VALID: dict[str, Any] = {
    "Image": "devagent-sandbox:dev",
    "Cmd": ["python", "-m", "pytest"],
    "User": "10001:10001",
    "Labels": {"devagent.managed": "true", "devagent.run_id": "r1"},
    "HostConfig": {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "Memory": 1024**3,
        "MemorySwap": 1024**3,
        "NanoCpus": 1_000_000_000,
        "PidsLimit": 256,
        "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=1000"},
        "Mounts": [
            {
                "Target": "/workspace",
                "Source": "devagent-workspaces",
                "Type": "volume",
                "VolumeOptions": {"Subpath": "r1/repo", "NoCopy": True},
            },
            {"Target": "/env", "Source": "/srv/workspaces/r1/env", "Type": "bind"},
        ],
    },
}


def mutate(path: str, value: Any) -> dict[str, Any]:
    body = copy.deepcopy(VALID)
    target = body
    *parents, leaf = path.split(".")
    for key in parents:
        target = target[key]
    if value is DELETE:
        del target[leaf]
    else:
        target[leaf] = value
    return body


DELETE = object()


def test_valid_sandbox_request_is_allowed() -> None:
    assert validate_create(VALID, SETTINGS) == []


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        ("Image", "alpine:latest", "image"),
        ("Labels", {}, "label"),
        ("User", "", "non-root"),
        ("User", "0:0", "non-root"),
        ("User", "root", "non-root"),
        ("HostConfig.Privileged", True, "privileged"),
        ("HostConfig.CapAdd", ["SYS_ADMIN"], "capabilities"),
        ("HostConfig.CapDrop", [], "CapDrop"),
        ("HostConfig.SecurityOpt", [], "no-new-privileges"),
        (
            "HostConfig.SecurityOpt",
            ["no-new-privileges:true", "seccomp=unconfined"],
            "security options",
        ),
        ("HostConfig.ReadonlyRootfs", False, "read-only"),
        ("HostConfig.PidMode", "host", "PidMode"),
        ("HostConfig.IpcMode", "container:postgres", "IpcMode"),
        ("HostConfig.UsernsMode", "host", "UsernsMode"),
        ("HostConfig.NetworkMode", "host", "network"),
        ("HostConfig.NetworkMode", "container:api", "network"),
        ("HostConfig.Runtime", "nvidia", "runtime"),
        ("HostConfig.Devices", [{"PathOnHost": "/dev/kmsg"}], "Devices"),
        ("HostConfig.Sysctls", {"kernel.x": "1"}, "Sysctls"),
        ("HostConfig.PortBindings", {"22/tcp": [{}]}, "PortBindings"),
        ("HostConfig.OomKillDisable", True, "OomKillDisable"),
        ("HostConfig.Memory", 0, "Memory"),
        ("HostConfig.Memory", DELETE, "Memory"),
        ("HostConfig.Memory", 64 * 1024**3, "Memory"),
        ("HostConfig.PidsLimit", -1, "PidsLimit"),
        ("HostConfig.NanoCpus", DELETE, "NanoCpus"),
        ("HostConfig.MemorySwap", -1, "swap"),
        ("HostConfig.Binds", ["/:/host"], "bind"),
        ("HostConfig.Binds", ["/var/run/docker.sock:/var/run/docker.sock"], "bind"),
        ("HostConfig.Binds", ["postgres-data:/data"], "bind"),
        (
            "HostConfig.Mounts",
            [{"Target": "/x", "Source": "/srv/workspaces/../../etc", "Type": "bind"}],
            "bind mount",
        ),
        (
            "HostConfig.Mounts",
            [{"Target": "/x", "Source": "/srv/workspaces-evil", "Type": "bind"}],
            "bind mount",
        ),
        (
            "HostConfig.Mounts",
            [
                {
                    "Target": "/x",
                    "Source": "devagent-workspaces",
                    "Type": "volume",
                    "VolumeOptions": {"Subpath": "../../postgres"},
                }
            ],
            "subpath",
        ),
        ("HostConfig.Mounts", [{"Target": "/x", "Source": "x", "Type": "npipe"}], "npipe"),
        ("HostConfig.VolumesFrom", ["api"], "VolumesFrom"),
        ("Volumes", {"/data": {}}, "anonymous"),
        ("NetworkingConfig", {"EndpointsConfig": {"devagent_default": {}}}, "endpoints"),
    ],
)
def test_unsafe_requests_are_rejected(path: str, value: Any, expected: str) -> None:
    problems = validate_create(mutate(path, value), SETTINGS)
    assert any(expected in p for p in problems), problems


def test_non_object_body() -> None:
    assert validate_create([1, 2], SETTINGS) == ["body must be a JSON object"]


def test_list_must_filter_on_managed_label() -> None:
    filters = json.dumps({"label": ["devagent.managed=true", "devagent.run_id=r1"]})
    assert _list_is_filtered(f"all=1&filters={filters}")
    assert _list_is_filtered("filters=" + json.dumps({"label": {"devagent.managed=true": True}}))
    assert not _list_is_filtered("all=1")
    assert not _list_is_filtered("filters=" + json.dumps({"label": ["other=true"]}))
    assert not _list_is_filtered("filters=not-json")


def test_request_parsing_strips_api_version_and_rewrites_connection() -> None:
    request = parse_head(
        b"POST /v1.54/containers/abc/kill?signal=KILL HTTP/1.1\r\n"
        b"Host: docker\r\nConnection: keep-alive\r\nContent-Length: 0"
    )
    assert isinstance(request, Request)
    assert request.path == "/containers/abc/kill"
    wire = request.encode(b"", keep_alive_upgrade=False)
    assert b"Connection: close\r\n" in wire
    assert b"keep-alive" not in wire
