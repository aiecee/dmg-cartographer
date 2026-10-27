"""Export writer: bounded JSON (+ optional Markdown and PNG files) under <data>/exports/<name>/. No emulator access."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dmg_cartographer.core.shared import atomic_write

MAX_JSON_BYTES = 2 * 1024 * 1024


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# Investigation: {report['rom']['title'] or report['rom']['file']}", "",
             f"- ROM SHA-1: `{report['rom']['sha1']}`",
             f"- Generated: {report['generated']}", "", "## Discoveries", ""]
    if not report["discoveries"]:
        lines.append("None saved.")
    for d in report["discoveries"]:
        lines.append(f"- **{d['name']}** [{d['status']}, confidence {d['confidence']}] "
                     f"{d.get('address') or '-'} {d.get('region') or ''}: {d.get('description', '')}")
        lines.append(f"  - evidence: {', '.join(d.get('evidence', [])) or 'none'}")
        if d.get("counterexamples"):
            lines.append(f"  - counterexamples: {', '.join(d['counterexamples'])}")
    lines += ["", "## Experiments", ""]
    for e in report["experiments"]:
        lines.append(f"- `{e['id']}` {e['name']}: frames {e['advanced_frames']}, changed addresses "
                     f"{e['changed_addresses']}")
    if report.get("candidates"):
        lines += ["", "## Candidate addresses (correlation only)", ""]
        for c in report["candidates"]["candidates"][:20]:
            lines.append(f"- `{c['address']}` action={c['action']} confidence={c['confidence']} "
                         f"evidence={len(c['evidence'])} counterexamples={len(c['counterexamples'])}")
    lines += ["", "## Replays", ""]
    lines += [f"- {r}" for r in report["replays"]] or ["None recorded."]
    lines += ["", "_Correlations and replay matches are evidence, not proof. Verify before relying on them._", ""]
    return "\n".join(lines)


def write_export(folder: Path, report: dict[str, Any], *, write_markdown: bool,
                 screenshots: dict[str, bytes]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError(f"export is {len(payload)} bytes, over the {MAX_JSON_BYTES} limit; "
                         f"export fewer experiments or candidates")
    files = {"json": str(folder / "analysis.json")}
    atomic_write(folder / "analysis.json", payload)
    if write_markdown:
        files["markdown"] = str(folder / "analysis.md")
        atomic_write(folder / "analysis.md", markdown(report).encode("utf-8"))
    for name, png in screenshots.items():
        path = folder / f"{name}.png"
        atomic_write(path, png)
        files.setdefault("screenshots", []).append(str(path))
    return {"files": files, "bytes": len(payload)}
