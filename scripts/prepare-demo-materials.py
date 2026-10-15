"""Prepare four fixed synthetic demo materials, without importing or running LifeCase."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "synthetic-cases" / "demo-v1" / "cases.json"
CASE_TYPES = ("warranty", "return_refund", "reimbursement", "subscription_cancellation")


def render_files() -> dict[str, bytes]:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if (
        manifest["synthetic"] is not True
        or tuple(c["type"] for c in manifest["cases"]) != CASE_TYPES
    ):
        raise ValueError("The fixed four-case synthetic fixture is invalid")
    files: dict[str, bytes] = {}
    lines = [
        "# LifeCase 四类离线演示材料",
        "",
        "全部为固定合成材料，无真实票据、账户、凭据或处理结果。日期不是当前期限建议。",
        "此目录只准备材料，不创建案件、导入材料、确认事实或登记行动；不会启动应用或模型。",
        "重复运行只复用同字节材料，不检查应用内是否已有案件，避免在页面重复建案或导入。",
        "",
        "在已有可用的隔离页面中，按下列类型和目标建案、导入对应 TXT。",
        "对照 cases.json 的 fields，从材料原文登记候选并逐项人工核实；不要把示例当作已确认事实。",
        "可在概览查看下一步，然后登记本地行动；审批、完成证据和解决案件分别操作。",
        "并非必须在一次演示中录入所有字段；未核实字段应继续显示待补。",
        "",
        "图片、视频和发票仅为文字说明，不含媒体本体、OCR 或真实凭证核验。",
        "取消确认编号也是示例原文，不表示订阅已取消；不要据此宣布真实事务成功。",
        "",
    ]
    for case in manifest["cases"]:
        filename = f"{case['type']}-synthetic.txt"
        text = "仅用于 LifeCase 隔离合成演示。\n" + "".join(
            f"{field}：{value}。\n" for field, value in case["fields"].items()
        )
        files[filename] = text.encode("utf-8")
        case["file"] = filename
        case["sha256"] = hashlib.sha256(files[filename]).hexdigest()
        lines.extend(
            [
                f"## {case['title']}",
                "",
                f"类型：`{case['type']}`。",
                f"目标：{case['goal']}。",
                f"材料：[{filename}]({filename})。",
                "",
            ]
        )
    files["cases.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    files["README.md"] = ("\n".join(lines) + "\n").encode("utf-8")
    return files


def prepare_materials(output: Path) -> str:
    if not output.is_absolute():
        raise ValueError("Pass an absolute --output directory outside the repository")
    if output.drive.startswith("\\\\"):
        raise ValueError("Network output paths are not supported")
    if any(p.is_symlink() or p.is_junction() for p in (output, *output.parents)):
        raise ValueError("Linked output directories are not supported")
    if output.resolve().is_relative_to(REPO.resolve()):
        raise ValueError("Output must stay outside the repository")
    files = render_files()
    if output.exists():
        if not output.is_dir():
            raise ValueError("Output is not a directory")
        existing = list(output.iterdir())
        if existing:
            if {p.name for p in existing} != set(files) or any(
                p.is_symlink()
                or p.is_junction()
                or not p.is_file()
                or p.read_bytes() != files[p.name]
                for p in existing
            ):
                raise ValueError("Output differs or contains other files; nothing was overwritten")
            return "reused"
    else:
        output.mkdir(parents=True)
    for name, content in files.items():
        with (output / name).open("xb") as target:
            target.write(content)
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        status = prepare_materials(args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Offline demo preparation stopped: {error}\n")
    print(json.dumps({"status": status, "output": str(args.output), "cases": 4}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
