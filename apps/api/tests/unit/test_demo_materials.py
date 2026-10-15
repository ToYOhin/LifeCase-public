"""Offline demo preparation must preserve user files and never import the app."""

import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "prepare-demo-materials.py"


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch):
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network is forbidden during offline preparation")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name == "lifecase" or name.startswith("lifecase."):
            raise AssertionError("Offline preparation must not import the application")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    spec = importlib.util.spec_from_file_location("demo_materials", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}


def test_four_cases_cover_current_templates_and_reuse_exact_materials(demo, tmp_path: Path):
    output = tmp_path / "demo"
    assert demo.prepare_materials(output) == "created"
    manifest = json.loads((output / "cases.json").read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert {case["type"] for case in manifest["cases"]} == {
        "warranty",
        "return_refund",
        "reimbursement",
        "subscription_cancellation",
    }
    assert len(manifest["cases"]) == 4
    for case in manifest["cases"]:
        template = yaml.safe_load(
            (REPO / "packages" / "case-templates" / f"{case['template_id']}.v1.yaml").read_text(
                encoding="utf-8"
            )
        )
        required = set(template["required_claims"]) | {
            item["key"] for item in template["evidence_requirements"]
        }
        assert required <= case["fields"].keys()
        allowed = required | {item["field"] for item in template["conditional_claims"]}
        assert case["fields"].keys() <= allowed
        evidence = (output / case["file"]).read_text(encoding="utf-8")
        assert hashlib.sha256((output / case["file"]).read_bytes()).hexdigest() == case["sha256"]
        assert all(str(value) in evidence for value in case["fields"].values())
    before = snapshot(output)
    assert demo.prepare_materials(output) == "reused"
    assert snapshot(output) == before


@pytest.mark.parametrize("change", ["edited", "unknown", "missing", "directory"])
def test_modified_or_incomplete_output_is_preserved(demo, tmp_path: Path, change: str):
    output = tmp_path / "demo"
    demo.prepare_materials(output)
    if change == "edited":
        (output / "warranty-synthetic.txt").write_text("用户修改", encoding="utf-8")
    elif change == "unknown":
        (output / "private.txt").write_text("保留的用户材料", encoding="utf-8")
    elif change == "missing":
        (output / "cases.json").unlink()
    else:
        (output / "warranty-synthetic.txt").unlink()
        (output / "warranty-synthetic.txt").mkdir()
    before = {p.name: p.read_bytes() if p.is_file() else None for p in output.iterdir()}
    with pytest.raises(ValueError):
        demo.prepare_materials(output)
    assert {p.name: p.read_bytes() if p.is_file() else None for p in output.iterdir()} == before


def test_empty_directory_is_accepted_but_relative_or_repository_path_is_not(demo, tmp_path: Path):
    output = tmp_path / "demo"
    output.mkdir()
    assert demo.prepare_materials(output) == "created"
    for unsafe in [Path("relative-demo"), REPO / "new-demo"]:
        with pytest.raises(ValueError):
            demo.prepare_materials(unsafe)
    assert not (REPO / "new-demo").exists()


def test_linked_output_does_not_touch_target(demo, tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating symbolic links")
    with pytest.raises(ValueError):
        demo.prepare_materials(link)
    with pytest.raises(ValueError):
        demo.prepare_materials(link / "nested")
    assert list(target.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UNC path semantics")
def test_network_output_is_rejected_before_filesystem_access(demo, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network path must be rejected before filesystem access")

    monkeypatch.setattr(Path, "is_symlink", forbidden)
    monkeypatch.setattr(Path, "is_junction", forbidden)
    with pytest.raises(ValueError, match="Network output"):
        demo.prepare_materials(Path(r"\\synthetic-host\share\demo"))
