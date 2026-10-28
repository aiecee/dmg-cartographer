"""KnowledgeMixin: the knowledge operations of PyBoyController."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dmg_cartographer.core.shared import MAX_RESULTS, _png, atomic_write
from dmg_cartographer import analysis
from dmg_cartographer import cartridge
from dmg_cartographer.knowledge import discoveries
from dmg_cartographer.knowledge import export
from dmg_cartographer import memwrite
from dmg_cartographer.knowledge import history
from dmg_cartographer.knowledge import replay


class KnowledgeMixin:
    """Methods for the knowledge part of the emulator controller."""

    # --- Persistent experiment and result history (see knowledge/history.py). ---

    def _history_file(self):
        return history.history_path(self._rom_dir, self._rom_sha1)

    def _save_history(self) -> None:
        """Write this ROM's experiments and stored results. Called whenever either changes."""
        if self._rom_dir is None or self._rom_sha1 is None:
            return
        experiments = [e for e in self._experiments.values() if e["rom_key"] == self._rom_key]
        results = [{"result_id": rid, "kind": r["kind"], "rom_key": r["rom_key"], "data": r["data"]}
                   for rid, r in self._results.items() if r["rom_key"] == self._rom_key]
        history.write_history(self._history_file(), experiments, results, dict(self._next_id))

    def _load_history(self) -> dict[str, int]:
        """Restore this ROM's saved experiments and results and move the ID counters past them."""
        saved = history.read_history(self._history_file())
        restored = {"experiments": 0, "results": 0}
        if saved is None:
            return restored
        for exp in sorted(saved["experiments"], key=lambda e: e["id"]):
            if exp["id"] not in self._experiments:
                self._experiments[exp["id"]] = exp
                restored["experiments"] += 1
        for res in sorted(saved["results"], key=lambda r: r["result_id"]):
            if res["result_id"] not in self._results:
                self._results[res["result_id"]] = {"kind": res["kind"], "rom_key": res["rom_key"],
                                                   "data": res["data"]}
                restored["results"] += 1
        for kind, number in saved["next_ids"].items():
            self._next_id[kind] = max(self._next_id.get(kind, 1), int(number))
        for kind, prefix in (("exp", "exp-"), ("result", "result-")):
            ids = [int(i[len(prefix):]) for i in list(self._experiments) + list(self._results)
                   if i.startswith(prefix)]
            if ids:
                self._next_id[kind] = max(self._next_id[kind], max(ids) + 1)
        self._trim(self._experiments, self._max_experiments)
        self._trim(self._results, MAX_RESULTS)
        return restored


    def _checkable_refs(self, rom_sha1: str, data_dir: Path) -> set[str]:
        refs = {e["id"] for e in self._experiments.values() if e["rom_key"] == self._rom_key}
        refs |= {rid for rid, r in self._results.items() if r["rom_key"] == self._rom_key and r["kind"] == "perturbation"}
        store = replay.ReplayStore(data_dir / "replays")
        refs |= {f"replay:{name}" for name in store.list(rom_sha1)}
        return refs


    def save_discovery(self, name: str, address: Any = None, region: str | None = None,
                       data_type: str | None = None, bank: int | None = None, confidence: str = "low",
                       description: str = "", evidence: list[str] | None = None,
                       counterexamples: list[str] | None = None, status: str = "hypothesis",
                       labels: list[str] | None = None, update: bool = False) -> dict[str, Any]:
        with self._lock:
            sha, rom_file, data_dir = self._loaded_identity()
            parsed = None if address is None else memwrite.parse_address(address)
            store = discoveries.DiscoveryStore(data_dir / "discoveries")
            data = store.read(sha, rom_file, self._pyboy.cartridge_title)
            if status == "verified" and parsed is not None:
                for ref in evidence or []:
                    stored = self._experiments.get(ref)
                    if stored is not None and parsed in stored.get("validation_contradicts", []):
                        raise ValueError(f"{ref} contradicted validation of 0x{parsed:04X}; it cannot support a "
                                         f"verified discovery for that address")
            existing = data["discoveries"].get(name)
            if existing is not None and not update:
                raise ValueError(f"discovery {name!r} already exists; pass update=true to change it")
            record = discoveries.validate_discovery(
                {"name": name, "address": parsed, "region": region, "data_type": data_type, "bank": bank,
                 "confidence": confidence, "description": description, "evidence": evidence,
                 "counterexamples": counterexamples, "status": status, "labels": labels},
                existing=existing if update else None, checkable_refs=self._checkable_refs(sha, data_dir))
            data["discoveries"][name] = record
            store.write(sha, data)
            return discoveries.summary(record, include_evidence=True)


    def get_discoveries(self, address: Any = None, region: str | None = None, label: str | None = None,
                        confidence: str | None = None, status: str | None = None, include_evidence: bool = False,
                        rom_sha1: str | None = None) -> dict[str, Any]:
        with self._lock:
            if rom_sha1 is None:
                sha, rom_file, data_dir = self._loaded_identity()
            else:
                if not re.fullmatch(r"[0-9a-f]{40}", rom_sha1):
                    raise ValueError("rom_sha1 must be a 40-character lowercase hex SHA-1")
                sha, data_dir = rom_sha1, self._folder_for_sha(rom_sha1)
            if confidence is not None and confidence not in discoveries.CONFIDENCE:
                raise ValueError(f"confidence must be one of {list(discoveries.CONFIDENCE)}")
            if status is not None and status not in discoveries.STATUSES:
                raise ValueError(f"status must be one of {list(discoveries.STATUSES)}")
            if region is not None and region not in discoveries.REGIONS:
                raise ValueError(f"region must be one of {list(discoveries.REGIONS)}")
            parsed = None if address is None else memwrite.parse_address(address)
            store = discoveries.DiscoveryStore(data_dir / "discoveries")
            data = store.read(sha)
            rows = [r for r in data["discoveries"].values()
                    if discoveries.matches(r, address=parsed, region=region, label=label,
                                           confidence=confidence, status=status)]
            rows.sort(key=lambda r: r["name"])
            out = {"rom_sha1": sha, "title": data.get("title"), **analysis.capped("discoveries", rows, 200)}
            out["discoveries"] = [discoveries.summary(r, include_evidence) for r in out["discoveries"]]
            return out


    def record_replay(self, name: str, mode: str = "record", checkpoint: str | None = None,
                      actions: list[dict[str, Any]] | None = None, expect: dict[str, int] | None = None) -> dict[str, Any]:
        """Record a deterministic run from a checkpoint, or replay a stored one and report any mismatch."""
        replay.validate_name(name)
        if mode not in ("record", "replay"):
            raise ValueError("mode must be 'record' or 'replay'")
        with self._lock:
            sha, rom_file, data_dir = self._loaded_identity()
            store = replay.ReplayStore(data_dir / "replays")
            stored = store.read(sha, name) if mode == "replay" else None
            if mode == "replay":
                if stored is None:
                    raise ValueError(f"no replay named {name!r} for this ROM; record it first")
                checkpoint = checkpoint or stored["checkpoint"]
                if checkpoint != stored["checkpoint"]:
                    raise ValueError(f"replay {name!r} starts from checkpoint {stored['checkpoint']!r}")
                steps = stored["steps"]
                expected = stored["expect"]
            else:
                if actions is None:
                    raise ValueError("record mode needs actions")
                steps = actions
                self._validate_sequence(steps)
                expected = expect or {}
            if checkpoint is None:
                raise ValueError("a checkpoint is required so the run starts from a known state")
            state_path, _ = self._checkpoint_paths(checkpoint)
            if not state_path.is_file():
                raise ValueError(f"checkpoint {checkpoint!r} does not exist for this ROM")
            if len(expected) > replay.MAX_EXPECT:
                raise ValueError(f"at most {replay.MAX_EXPECT} expected values")
            expect_addrs = {memwrite.parse_address(a): memwrite.check_values([v])[0] for a, v in expected.items()}

            with self._restoring():
                self.load_checkpoint(checkpoint)
                start = self._fingerprint()
                sequence = self.run_sequence(steps)
                end = self._fingerprint()
                observed_expect = {f"0x{a:04X}": int(self._pyboy.memory[a]) for a in expect_addrs}

            if mode == "record":
                wrong = [f"0x{a:04X}: expected {v}, observed {observed_expect[f'0x{a:04X}']}"
                         for a, v in expect_addrs.items() if observed_expect[f"0x{a:04X}"] != v]
                if wrong:
                    raise ValueError(f"expect values did not hold in the recording run (nothing stored): {wrong}")
                record = {"name": name, "rom_sha1": sha, "rom_file": rom_file, "checkpoint": checkpoint,
                          "steps": steps, "start": start, "end": end,
                          "expect": {f"0x{a:04X}": v for a, v in expect_addrs.items()},
                          "requested_frames": sequence["requested_frames"], "recorded": replay.now()}
                store.write(sha, name, record)
                return {"mode": "record", "name": name, "stored": True, "checkpoint": checkpoint,
                        "requested_frames": sequence["requested_frames"], "end": end,
                        "evidence_ref": f"replay:{name}"}
            mismatches = ([{**m, "field": f"start.{m['field']}"} for m in replay.compare(stored["start"], start)]
                          + [{**m, "field": f"end.{m['field']}"} for m in replay.compare(stored["end"], end)])
            for addr, want in stored["expect"].items():
                got = observed_expect[addr]
                if got != want:
                    mismatches.append({"field": f"expect.{addr}", "recorded": want, "observed": got})
            advanced = end["frame"] - start["frame"]
            out = {"mode": "replay", "name": name, "completed": True, "matched": not mismatches,
                   **analysis.capped("mismatches", mismatches, 50),
                   "requested_frames": sequence["requested_frames"], "advanced_frames": advanced,
                   "recorded_frames": stored["requested_frames"],
                   "frames_match_recording": advanced == stored["requested_frames"],
                   "possible_sources": self._divergence_sources(mismatches),
                   "fingerprint_scope": ("WRAM, HRAM, frame count and held buttons, read through PyBoy's public "
                                         "memory API. Not VRAM, OAM, I/O registers, timers or audio."),
                   "restored": True, "evidence_ref": f"replay:{name}"}
            out["result_id"] = self._store_result("replay", out)
            return out

    @staticmethod
    def _divergence_sources(mismatches: list[dict[str, Any]]) -> list[str]:
        """Plain-language candidate causes for a replay mismatch. Each is a hypothesis to check, not a diagnosis."""
        if not mismatches:
            return ["no divergence in the fingerprinted state (WRAM, HRAM, frame count, held buttons); "
                    "this shows repeatability for that state only"]
        fields = [m["field"] for m in mismatches]
        sources = []
        if any(f.startswith("start.") for f in fields):
            sources.append("starting state differs: the checkpoint changed, or the emulator started from a different state")
        if any(f.endswith("frame") for f in fields):
            sources.append("frame count differs: a recorded input landed on a different frame")
        if any(f.endswith("held") for f in fields):
            sources.append("held buttons differ: input timing or button state changed")
        if any(f.startswith("expect.") for f in fields):
            sources.append("an expected value differs: the game took a different path")
        memory_only = any(".regions." in f for f in fields) and not any(f.endswith(("frame", "held")) for f in fields)
        if memory_only:
            sources.append("WRAM/HRAM differ while frames and buttons match: state outside the fingerprint "
                           "(VRAM, OAM, I/O registers, timers, audio) or timing inside the game differs")
        sources.append("PyBoy and the game were not verified to be fully deterministic; a mismatch may also come "
                       "from the ROM file or the host")
        return sources


    def export_analysis(self, name: str, candidates: list[str] | None = None, include_screenshots: bool = False,
                        markdown: bool = True, max_experiments: int = 200) -> dict[str, Any]:
        replay.validate_name(name)
        if isinstance(max_experiments, bool) or not isinstance(max_experiments, int) or not 1 <= max_experiments <= 500:
            raise ValueError("max_experiments must be 1..500")
        with self._lock:
            sha, rom_file, data_dir = self._loaded_identity()
            header = cartridge.parse_header(self._rom_path.read_bytes())
            disc = discoveries.DiscoveryStore(data_dir / "discoveries").read(sha, rom_file, self._pyboy.cartridge_title)
            rstore = replay.ReplayStore(data_dir / "replays")
            replays = []
            for rname in rstore.list(sha):
                rec = rstore.read(sha, rname)
                replays.append({"name": rname, "checkpoint": rec["checkpoint"], "steps": len(rec["steps"]),
                                "requested_frames": rec["requested_frames"], "recorded": rec["recorded"]})
            experiments = [self._experiment_meta(e) for e in self._experiments.values()
                           if e["rom_key"] == self._rom_key][-max_experiments:]
            candidate_report = None
            if candidates:
                candidate_report = self.find_memory_candidates(candidates, max_results=50)
            report = {
                "generated": replay.now(),
                "rom": {"file": rom_file, "title": self._pyboy.cartridge_title, "sha1": sha,
                        "bytes": header.get("rom_size", {}).get("file_bytes"), "header": header},
                "discoveries": sorted(disc["discoveries"].values(), key=lambda r: r["name"])[:500],
                "experiments": experiments,
                "snapshots": [self._snapshot_meta(s) for s in self._snapshots.values()][-100:],
                "search_sessions": self.list_search_sessions()[-50:],
                "replays": replays,
                "candidates": candidate_report,
                "limits": ("Correlations, screen changes and replay matches are evidence, not proof. Replays check "
                           "WRAM/HRAM, frame count and buttons only."),
            }
            screenshots = {}
            if include_screenshots:
                screenshots["screen-current"] = _png(analysis.normalise_screen(self._pyboy.screen.image))
            folder = data_dir / "exports" / name
            written = export.write_export(folder, report, write_markdown=markdown, screenshots=screenshots)
            return {"name": name, "folder": str(folder), **written,
                    "counts": {"discoveries": len(report["discoveries"]), "experiments": len(experiments),
                               "replays": len(replays), "snapshots": len(report["snapshots"])}}


    def export_discoveries(self, name: str) -> dict[str, Any]:
        """Write this ROM's discoveries to exports/<name>/discoveries.json (structured, bounded)."""
        replay.validate_name(name)
        with self._lock:
            sha, rom_file, data_dir = self._loaded_identity()
            data = discoveries.DiscoveryStore(data_dir / "discoveries").read(sha, rom_file, self._pyboy.cartridge_title)
            rows = sorted(data["discoveries"].values(), key=lambda r: r["name"])[:500]
            payload = {"rom": {"file": rom_file, "sha1": sha, "title": self._pyboy.cartridge_title},
                       "exported": replay.now(), "discoveries": rows,
                       "note": "Saved hypotheses. Status is only as strong as the cited experiments and replays."}
            folder = data_dir / "exports" / name
            body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            atomic_write(folder / "discoveries.json", body)
            return {"name": name, "file": str(folder / "discoveries.json"), "count": len(rows), "bytes": len(body)}

    def inspect_cartridge(self) -> dict[str, Any]:
        with self._lock:
            sha, rom_file, _ = self._loaded_identity()
            data = self._rom_path.read_bytes()
            header = cartridge.parse_header(data)
            emulator_title = self._pyboy.cartridge_title
            title_ok = cartridge.title_matches(header.get("title", ""), emulator_title) if header.get("valid_header") else None
            checks = {"header_valid": bool(header.get("valid_header")),
                      "header_checksum": bool(header.get("header_checksum", {}).get("valid")),
                      "rom_size_matches_file": bool(header.get("rom_size", {}).get("matches_file")),
                      "title_matches_emulator": title_ok}
            return {"file": rom_file, "rom_sha1": sha, "rom_bytes": len(data), "header": header,
                    "emulator_title": emulator_title, "checks": checks,
                    "all_checks_pass": all(v is True for v in checks.values()),
                    "note": ("Facts come from the ROM file and header. The bank controller's runtime banking is "
                             "not exposed by PyBoy's public API, so only the declared type is reported.")}
