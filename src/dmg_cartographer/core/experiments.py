"""ExperimentMixin: the experiments operations of PyBoyController."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from dmg_cartographer.core.shared import (MAX_RESULTS, BRANCH_COMPARE, MAX_BATCH_FRAMES, MAX_BRANCHES, MAX_FRAMES, read_oam,
                         read_registers, tile_rect_in_map)
from dmg_cartographer import analysis
from dmg_cartographer import graphics
from dmg_cartographer import memwrite


class ExperimentMixin:
    """Methods for the experiments part of the emulator controller."""

    @staticmethod
    def _experiment_meta(exp: dict[str, Any]) -> dict[str, Any]:
        meta = {k: exp[k] for k in ("id", "name", "rom", "before", "after", "start_frame", "end_frame",
                                    "advanced_frames", "requested_frames", "completed_actions", "timestamp")}
        meta["changed_addresses"] = None if exp["changes"] is None else len(exp["changes"])
        meta["actions"] = exp.get("actions")
        if "screen_summary" in exp:
            meta["screen_summary"] = exp["screen_summary"]
        return meta


    def run_experiment(self, name: str, actions: list[dict[str, Any]], compare_memory: bool = True,
                       compare_screen: bool = True, max_results: int = 50) -> dict[str, Any]:
        """Snapshot, run `actions` with no extra frames, snapshot again, and compare."""
        with self._lock:
            self._require_game()
            self._check_label(name, "experiment")
            if not (compare_memory or compare_screen):
                raise ValueError("enable compare_memory and/or compare_screen")
            self._validate_sequence(actions)  # reject bad input before capturing anything

            before, evicted = self._store_snapshot(f"{name}:before", self._read_state(compare_memory, compare_screen, False))
            original = self._capture_live_state()
            try:
                sequence = self.run_sequence(actions)
            except BaseException:
                # A failed experiment must not leave frames advanced or buttons held.
                self._restore_live_state(original)
                raise
            after, evicted_after = self._store_snapshot(f"{name}:after", self._read_state(compare_memory, compare_screen, False))
            evicted += evicted_after

            exp: dict[str, Any] = {
                "id": self._new_id("exp"), "name": name, "rom": self._rom_name, "rom_key": self._rom_key,
                "before": before["id"], "after": after["id"],
                "start_frame": before["frame"], "end_frame": after["frame"],
                "advanced_frames": after["frame"] - before["frame"],
                "requested_frames": sequence["requested_frames"], "completed_actions": sequence["completed_actions"],
                "timestamp": after["timestamp"], "changes": None, "actions": actions,
            }
            result: dict[str, Any] = {}
            if compare_memory:
                changes: dict[int, tuple[int, int]] = {}
                result["memory"] = {}
                for region, (start, _) in analysis.REGIONS.items():
                    if region in before["memory"] and region in after["memory"]:
                        changes.update(analysis.changed_bytes(before["memory"][region], after["memory"][region], start))
                        result["memory"][region] = analysis.compare_memory(
                            before["memory"][region], after["memory"][region], start, max_results=max_results)
                    else:
                        result["memory"][region] = {"error": before["memory_errors"].get(region)
                                                    or after["memory_errors"].get(region, "unreadable")}
                exp["changes"] = changes
            if compare_screen:
                screen, _ = analysis.compare_screen(before["screen"], after["screen"], max_items=max_results)
                if not before["screen_fresh"]:
                    screen["warning"] = self.STALE_SCREEN
                result["screen"] = screen
                exp["screen_summary"] = {k: screen.get(k) for k in ("changed_pixels", "percent_changed", "bounding_box")}
            self._experiments[exp["id"]] = exp
            evicted += self._trim(self._experiments, self._max_experiments)
            self._save_history()

            out = {**self._experiment_meta(exp), "held_buttons": sequence["held_buttons"],
                   "emulator_running": sequence["emulator_running"], **result}
            if evicted:
                out["evicted"] = evicted
            return out


    def list_experiments(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._experiment_meta(e) for e in self._experiments.values()]


    def find_memory_candidates(self, experiments: list[str], expected_change: dict[str, str | int] | None = None,
                               max_results: int = 50) -> dict[str, Any]:
        """`experiments` holds ids or names; a name selects every stored experiment with that name."""
        with self._lock:
            if not isinstance(experiments, list) or not experiments:
                raise ValueError("experiments must be a non-empty list of experiment ids or names")
            chosen = self._select_experiments(experiments)
            no_memory = [e["id"] for e in chosen if e["changes"] is None]
            if no_memory:
                raise ValueError(f"experiments ran with compare_memory=False: {no_memory}")
            result = analysis.find_memory_candidates(chosen, expected_change, max_results)
            actions_by_name: dict[str, Any] = {}
            for e in chosen:
                actions_by_name.setdefault(e["name"], e.get("actions"))
            result["suggested_validation"] = [
                s for s in (self._suggest_validation(c, chosen) for c in result["candidates"][:5]) if s]
            return result

    @staticmethod
    def _expectation_from_diffs(diffs: list[int]) -> str | None:
        """A rule word that every observed diff satisfies, or None when the observations disagree."""
        if not diffs:
            return None
        if all(d == 0 for d in diffs):
            return "none"
        if all(d > 0 for d in diffs):
            return "increase"
        if all(d < 0 for d in diffs):
            return "decrease"
        if all(d != 0 for d in diffs):
            return "change"
        return None  # some runs changed the value and some did not: no single rule fits

    def _suggest_validation(self, candidate: dict[str, Any], chosen: list[dict[str, Any]]) -> dict[str, Any] | None:
        """A ready-to-run investigate_validate call, built from every chosen experiment (unchanged counts as 0)."""
        address = int(candidate["address"], 16)
        by_name: dict[str, list[int]] = {}
        actions_by_name: dict[str, Any] = {}
        for e in chosen:
            prev_cur = e["changes"].get(address)
            diff = 0 if prev_cur is None else prev_cur[1] - prev_cur[0]
            by_name.setdefault(e["name"], []).append(diff)
            actions_by_name.setdefault(e["name"], e.get("actions"))
        expected = {}
        for name, diffs in by_name.items():
            rule = self._expectation_from_diffs(diffs)
            if rule is not None and actions_by_name.get(name) is not None:
                expected[name] = rule
        if not expected:
            return None
        return {"address": candidate["address"], "expected": expected,
                "experiments": {n: actions_by_name[n] for n in expected},
                "repeats": 2,
                "next": ("run investigate_validate from the same checkpoint with these arguments. A supported "
                         "result is still an experiment result, not a verified discovery.")}


    def _select_experiments(self, refs: list[str]) -> list[dict[str, Any]]:
        """Resolve experiment ids or names (a name selects every stored experiment with that name)."""
        if not isinstance(refs, list) or not refs:
            raise ValueError("experiments must be a non-empty list of experiment ids or names")
        chosen: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for ref in refs:
            if ref in self._experiments:
                chosen[ref] = self._experiments[ref]
                continue
            named = [e for e in self._experiments.values() if e["name"] == ref]
            if not named:
                raise ValueError(f"unknown experiment {ref!r}; call list_experiments")
            for e in named:
                chosen[e["id"]] = e
        if len({e["rom_key"] for e in chosen.values()}) > 1:
            raise ValueError("experiments come from different ROMs")
        return list(chosen.values())


    def branch_experiment(self, checkpoint: str, branches: list[dict[str, Any]],
                          compare: list[str] | None = None, max_results: int = 50) -> dict[str, Any]:
        """Run each branch's actions from the same checkpoint, compare against the checkpoint state and each other.

        The live emulator state (frames, held buttons, memory) is restored afterwards, even when a branch fails.
        """
        compare = list(BRANCH_COMPARE) if compare is None else compare
        if not isinstance(compare, list) or not compare:
            raise ValueError(f"compare must be a non-empty list from {list(BRANCH_COMPARE)}")
        if "sprites" in compare:
            raise ValueError("compare 'sprites' is not implemented yet (planned with inspect_sprites)")
        unknown = [c for c in compare if c not in BRANCH_COMPARE]
        if unknown:
            raise ValueError(f"unknown compare entries {unknown}; choose from {list(BRANCH_COMPARE)}")
        if not isinstance(branches, list) or not 1 <= len(branches) <= MAX_BRANCHES:
            raise ValueError(f"Provide 1 to {MAX_BRANCHES} branches")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 500:
            raise ValueError("max_results must be 1..500")
        normalized: list[tuple[str, list[dict[str, Any]]]] = []
        names: set[str] = set()
        for branch in branches:
            if not isinstance(branch, dict):
                raise ValueError("Each branch must be an object with name and actions")
            name = self._check_label(branch.get("name"), "branch")
            if name in names:
                raise ValueError(f"duplicate branch name {name!r}")
            names.add(name)
            actions = branch.get("actions")
            self._validate_sequence(actions)  # reject bad input before touching the emulator
            normalized.append((name, actions))

        memory = "memory" in compare
        screen = "screen" in compare
        with self._lock:
            self._require_game()
            state_path, _ = self._checkpoint_paths(checkpoint)
            if not state_path.is_file() or state_path.is_symlink():
                raise ValueError(f"checkpoint {checkpoint!r} does not exist for this ROM")
            with self._restoring():
                baseline = None
                results: dict[str, dict[str, Any]] = {}
                branch_regions: dict[str, dict[str, bytes]] = {}
                branch_screens: dict[str, Any] = {}
                for name, actions in normalized:
                    self.load_checkpoint(checkpoint)
                    if baseline is None:
                        baseline = self._read_state(memory, screen, False)
                    start_frame = self._frames
                    sequence = self.run_sequence(actions)
                    after = self._read_state(memory, screen, False)
                    results[name] = {
                        "start_frame": start_frame, "end_frame": self._frames,
                        "requested_frames": sequence["requested_frames"],
                        "advanced_frames": self._frames - start_frame,
                        "completed_actions": sequence["completed_actions"],
                        "held_buttons": sequence["held_buttons"], "emulator_running": sequence["emulator_running"],
                    }
                    if memory:
                        branch_regions[name] = after["memory"]
                    if screen:
                        branch_screens[name] = after["screen"]
                    if not after["screen_fresh"]:
                        results[name]["warning"] = self.STALE_SCREEN
                comparison = analysis.compare_branches(
                    baseline["memory"] if memory else {}, branch_regions, max_results) if memory else {}
                if screen:
                    for name, shot in branch_screens.items():
                        diff, _ = analysis.compare_screen(baseline["screen"], shot, mode="both", max_items=max_results)
                        comparison.setdefault("branches", {}).setdefault(name, {})["screen"] = diff
                for name, row in results.items():
                    if name in comparison.get("branches", {}):
                        row.update(comparison["branches"][name])
                out = {"checkpoint": checkpoint, "baseline_frame": baseline["frame"],
                       "compare": compare, "branches": [{"name": n, **results[n]} for n, _ in normalized],
                       "shared_change_count": comparison.get("shared_change_count", 0),
                       "restored": True}
                out["result_id"] = self._store_result("branch", out)
                return out


    def watch_memory(self, addresses: list[Any], frames: int, sample_every: int = 1,
                     record_changes_only: bool = True, actions: list[dict[str, Any]] | None = None,
                     max_events: int = 200) -> dict[str, Any]:
        """Memory-only watch, kept for compatibility. investigate_watch takes the wider monitor set."""
        return self.investigate_watch({"addresses": addresses}, frames, sample_every, record_changes_only,
                                      actions, max_events)

    WATCH_KINDS = frozenset({"addresses", "sprites", "tiles", "screen_region", "cpu"})

    def investigate_watch(self, monitors: dict[str, Any], frames: int, sample_every: int = 1,
                          record_changes_only: bool = True, actions: list[dict[str, Any]] | None = None,
                          max_events: int = 200) -> dict[str, Any]:
        """Advance exactly `frames` frames and report changes in the chosen monitors. Frame sampling only."""
        if not isinstance(monitors, dict) or not monitors:
            raise ValueError("monitors must be an object with at least one of: " + ", ".join(sorted(self.WATCH_KINDS)))
        unknown = set(monitors) - self.WATCH_KINDS
        if unknown:
            raise ValueError(f"unknown monitor kinds {sorted(unknown)}")
        parsed_addresses: list[int] = []
        if "addresses" in monitors:
            if not isinstance(monitors["addresses"], list) or not 1 <= len(monitors["addresses"]) <= 16:
                raise ValueError("addresses must be a list of 1..16 addresses")
            parsed_addresses = [memwrite.parse_address(a) for a in monitors["addresses"]]
            if len(set(parsed_addresses)) != len(parsed_addresses):
                raise ValueError("duplicate addresses")
        sprite_slots: list[int] = []
        if "sprites" in monitors:
            slots = monitors["sprites"]
            if not isinstance(slots, list) or not 1 <= len(slots) <= 8 or len(set(slots)) != len(slots) or any(
                    isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < graphics.SPRITE_COUNT for i in slots):
                raise ValueError("sprites must be 1..8 distinct OAM slots 0..39")
            sprite_slots = list(slots)
        tiles = None
        if "tiles" in monitors:
            t = monitors["tiles"]
            if not isinstance(t, dict) or set(t) != {"layer", "x", "y", "width", "height"}:
                raise ValueError("tiles needs layer, x, y, width, height")
            if t["layer"] not in ("background", "window"):
                raise ValueError("tiles layer must be 'background' or 'window'")
            for key in ("x", "y", "width", "height"):
                if isinstance(t[key], bool) or not isinstance(t[key], int):
                    raise ValueError(f"tiles {key} must be an integer")
            if not tile_rect_in_map(t["x"], t["y"], t["width"], t["height"]) or t["width"] * t["height"] > 64:
                raise ValueError("tiles rectangle must lie in the 32x32 map and have at most 64 tiles")
            tiles = dict(t)
        screen = None
        if "screen_region" in monitors:
            screen = self._check_screen_region(monitors["screen_region"])
        cpu = monitors.get("cpu", False)
        if not isinstance(cpu, bool):
            raise ValueError("cpu must be true or false")
        if not (parsed_addresses or sprite_slots or tiles or screen or cpu):
            raise ValueError("choose at least one monitor")
        if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= MAX_BATCH_FRAMES:
            raise ValueError(f"frames must be an integer 1..{MAX_BATCH_FRAMES}")
        if isinstance(sample_every, bool) or not isinstance(sample_every, int) or not 1 <= sample_every <= frames:
            raise ValueError("sample_every must be an integer 1..frames")
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= 1000:
            raise ValueError("max_events must be 1..1000")
        with self._lock:
            game = self._require_game()
            cgb = bool(self._header_cgb(game).get("cgb_compatible"))
            if actions is None:
                schedule = [(frames, [], [])]
            else:
                schedule = self._validate_sequence(actions)
                if sum(c for c, _, _ in schedule) != frames:
                    raise ValueError("the actions must advance exactly `frames` frames in total")

            def read() -> tuple[dict[tuple, Any], dict[str, Any]]:
                values: dict[tuple, Any] = {}
                images: dict[str, Any] = {}
                for a in parsed_addresses:
                    values[("memory", a)] = int(game.memory[a])
                if sprite_slots:
                    oam, lcdc = read_oam(game)
                    for row in graphics.sprite_rows(oam, lcdc, cgb=cgb, indices=sprite_slots):
                        values[("sprite", row["index"])] = (row["screen"]["x"], row["screen"]["y"],
                                                            row["tile_index"], row["raw"]["attributes"])
                if tiles is not None:
                    tilemap = game.tilemap_background if tiles["layer"] == "background" else game.tilemap_window
                    for row in range(tiles["y"], tiles["y"] + tiles["height"]):
                        for col in range(tiles["x"], tiles["x"] + tiles["width"]):
                            values[("tile", tiles["layer"], col, row)] = int(tilemap[col, row])
                if screen is not None:
                    crop = analysis.normalise_screen(game.screen.image).crop(
                        (screen["x"], screen["y"], screen["x"] + screen["width"], screen["y"] + screen["height"]))
                    images["screen"] = crop
                    values[("screen_region",)] = crop.tobytes()
                if cpu:
                    for reg, value in read_registers(game).items():
                        values[("cpu", reg)] = value
                return values, images

            def event(key: tuple, previous: Any, current: Any, offset: int, prev_img: Any, now_img: Any) -> dict:
                kind = key[0]
                if kind == "memory":
                    return {"kind": "memory", "frame": offset, "address": f"0x{key[1]:04X}",
                            "previous": previous, "current": current}
                if kind == "sprite":
                    def as_dict(v):
                        return None if v is None else {"x": v[0], "y": v[1], "tile": v[2], "attributes": v[3]}
                    return {"kind": "sprite", "frame": offset, "slot": key[1],
                            "previous": as_dict(previous), "current": as_dict(current)}
                if kind == "tile":
                    return {"kind": "tile", "frame": offset, "layer": key[1],
                            "tilemap": {"col": key[2], "row": key[3]}, "previous": previous, "current": current}
                if kind == "cpu":
                    return {"kind": "cpu", "frame": offset, "register": key[1],
                            "previous": previous, "current": current}
                changed = 0 if prev_img is None or now_img is None else analysis.changed_pixel_count(prev_img, now_img)
                return {"kind": "screen_region", "frame": offset, "region": screen, "changed_pixels": changed}

            start = self._frames
            last, last_images = read()
            events: list[dict[str, Any]] = []
            inputs: list[dict[str, Any]] = []
            change_count = 0
            samples = 0
            since_sample = 0

            def sample() -> None:
                nonlocal last, last_images, change_count, samples
                now, now_images = read()
                samples += 1
                offset = self._frames - start
                for key, value in now.items():
                    changed = value != last.get(key)
                    if changed:
                        change_count += 1
                    if changed or not record_changes_only:
                        if len(events) < max_events:
                            events.append(event(key, last.get(key), value, offset,
                                                last_images.get("screen"), now_images.get("screen")))
                last, last_images = now, now_images

            for count, press, release in schedule:
                if press or release:
                    if len(inputs) < max_events:
                        inputs.append({"frame": self._frames - start, "press": list(press),
                                       "release": list(release)})
                remaining = count
                first = True
                while remaining > 0:
                    chunk = min(remaining, sample_every - since_sample)
                    self.step(chunk, press if first else None, release if first else None)
                    first = False
                    remaining -= chunk
                    since_sample += chunk
                    if since_sample == sample_every:
                        sample()
                        since_sample = 0
            if since_sample:
                sample()  # final partial window, so the end state is always observed
            monitored = {"addresses": [f"0x{a:04X}" for a in parsed_addresses], "sprites": sprite_slots,
                         "tiles": tiles, "screen_region": screen, "cpu": cpu}
            return {
                "start_frame": start, "end_frame": self._frames, "advanced_frames": self._frames - start,
                "requested_frames": frames, "sample_every": sample_every, "samples": samples,
                "change_count": change_count, "record_changes_only": record_changes_only,
                "monitors": monitored,
                "addresses": [{"address": f"0x{a:04X}", "region": memwrite.region_of(a)[0]} for a in parsed_addresses],
                "events": events, "events_truncated": change_count > len(events) and record_changes_only,
                "input_events": inputs,
                "limitation": ("frame-sampled: a value written and restored within one sampling window, or several "
                               "times within one frame, is not reported. Sprite, tile and screen monitors see the "
                               "sampled frame only. Not instruction-level."),
            }

    def get_result(self, result_id: str) -> dict[str, Any]:
        """Stored results of branch and replay runs, kept for this session (see the result_id field)."""
        with self._lock:
            if result_id not in self._results:
                raise ValueError(f"unknown result {result_id!r}; results are kept in memory for this session")
            entry = self._results[result_id]
            return {"result_id": result_id, "kind": entry["kind"], "rom_key": entry["rom_key"], **entry["data"]}

    def _store_result(self, kind: str, data: dict[str, Any]) -> str:
        result_id = self._new_id("result")
        self._results[result_id] = {"kind": kind, "rom_key": self._rom_key, "data": data}
        self._trim(self._results, MAX_RESULTS)
        self._save_history()
        return result_id

    def investigate_correlations(self, experiments: list[str], max_results: int = 20) -> dict[str, Any]:
        with self._lock:
            chosen = self._select_experiments(experiments)
            usable = [e for e in chosen if e["changes"] is not None and e.get("screen_summary") is not None]
            excluded = [{"id": e["id"], "reason": "needs compare_memory=true and compare_screen=true"}
                        for e in chosen if e not in usable]
            if len(usable) < 2:
                raise ValueError("need at least 2 experiments that have both memory and screen comparisons")
            records = [{"id": e["id"], "name": e["name"], "changes": e["changes"],
                        "screen_changed": e["screen_summary"]["changed_pixels"] > 0} for e in usable]
            result = analysis.correlate_with_screen(records, max_results)
            return {
                **result,
                "experiments": [e["id"] for e in usable], "excluded": excluded,
                "frame_counts": {e["id"]: e["advanced_frames"] for e in usable},
                "input_events": {e["id"]: e.get("actions") for e in usable},
                "screen_change_rule": "changed_pixels > 0 between the experiment's before and after frames",
            }


    def investigate_validate(self, address: Any, checkpoint: str, experiments: dict[str, list[dict[str, Any]]],
                             expected: dict[str, Any], repeats: int = 2,
                             screen_expected: dict[str, str] | None = None,
                             perturb: dict[str, int] | None = None, allow_writes: bool = False) -> dict[str, Any]:
        """Run named action sequences from one checkpoint and check them against a hypothesis about one address.

        The hypothesis is only ever reported as supported by these experiments. Live state is restored afterwards.
        """
        addr = memwrite.parse_address(address)
        if not isinstance(experiments, dict) or not 1 <= len(experiments) <= 8:
            raise ValueError("experiments must be an object of 1-8 named action sequences")
        for name, actions in experiments.items():
            self._check_label(name, "experiment")
            self._validate_sequence(actions)
        if not isinstance(expected, dict) or not expected or any(k not in experiments for k in expected):
            raise ValueError("expected must map experiment names (which must exist in experiments) to rules")
        for name, rule in expected.items():
            if not (rule in analysis.EXPECTATIONS or (isinstance(rule, int) and not isinstance(rule, bool))):
                raise ValueError(f"rule for {name!r} must be one of {list(analysis.EXPECTATIONS)} or an integer")
        screen_expected = screen_expected or {}
        for name, rule in screen_expected.items():
            if name not in experiments or rule not in ("changes", "static"):
                raise ValueError("screen_expected maps experiment names to 'changes' or 'static'")
        if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 10:
            raise ValueError("repeats must be 1..10")
        if perturb is not None:
            if not allow_writes:
                raise ValueError("perturb writes memory; pass allow_writes=true to authorise it")
            if set(perturb) != {"value", "frames"}:
                raise ValueError("perturb needs exactly value and frames")
            memwrite.check_write(addr, 1, allow_sensitive=False)
            memwrite.check_values([perturb["value"]])
            if isinstance(perturb["frames"], bool) or not isinstance(perturb["frames"], int) \
                    or not 0 <= perturb["frames"] <= MAX_FRAMES:
                raise ValueError(f"perturb frames must be 0..{MAX_FRAMES}")
        observed_region = any(start <= addr < end for start, end in analysis.REGIONS.values())
        if not observed_region:
            raise ValueError("the address must be in WRAM or HRAM; other regions are not compared")
        with self._lock:
            self._require_game()
            state_path, _ = self._checkpoint_paths(checkpoint)
            if not state_path.is_file():
                raise ValueError(f"checkpoint {checkpoint!r} does not exist for this ROM")
            with self._restoring():
                runs = []
                for _ in range(repeats):
                    for name, actions in experiments.items():
                        self.load_checkpoint(checkpoint)
                        result = self.run_experiment(name, actions, True, True, max_results=1)
                        stored = self._experiments[result["id"]]
                        prev_cur = stored["changes"].get(addr)
                        diff = None if prev_cur is None else prev_cur[1] - prev_cur[0]
                        runs.append({"experiment": stored["id"], "name": name,
                                     "advanced_frames": stored["advanced_frames"],
                                     "diff": 0 if diff is None else diff,
                                     "previous": None if prev_cur is None else prev_cur[0],
                                     "current": None if prev_cur is None else prev_cur[1],
                                     "screen_changed": stored["screen_summary"]["changed_pixels"] > 0})
                perturbation = None
                if perturb is not None:
                    self.load_checkpoint(checkpoint)
                    before = self._read_state(True, True, False)
                    self._pyboy.memory[addr] = perturb["value"]
                    self.step(perturb["frames"])
                    after = self._read_state(True, True, False)
                    screen, _ = analysis.compare_screen(before["screen"], after["screen"], mode="pixel")
                    perturbation = {"address": f"0x{addr:04X}", "checkpoint": checkpoint,
                                    "value_written": perturb["value"], "frames_advanced": perturb["frames"],
                                    "value_after": int(self._pyboy.memory[addr]),
                                    "reverted": int(self._pyboy.memory[addr]) != perturb["value"],
                                    "screen_changed": screen["changed_pixels"] > 0,
                                    "note": "an observation after a reversible write; the emulator is restored after"}
                    perturbation["result_id"] = self._store_result("perturbation", dict(perturbation))

            per_action: dict[str, dict[str, int]] = {}
            for name in experiments:
                rows = [r for r in runs if r["name"] == name]
                per_action[name] = {"runs": len(rows), "confirmed": 0, "contradicted": 0}
            contradictions: list[dict[str, Any]] = []
            confirmed_runs: list[str] = []
            for r in runs:
                if r["name"] in expected:
                    rule = expected[r["name"]]
                    if analysis.meets(rule, r["diff"]):
                        per_action[r["name"]]["confirmed"] += 1
                        confirmed_runs.append(r["experiment"])
                    else:
                        per_action[r["name"]]["contradicted"] += 1
                        contradictions.append({"experiment": r["experiment"], "check": "value",
                                               "diff": r["diff"], "expected": rule})
                if r["name"] in screen_expected:
                    want_change = screen_expected[r["name"]] == "changes"
                    if r["screen_changed"] == want_change:
                        confirmed_runs.append(r["experiment"])
                    else:
                        contradictions.append({"experiment": r["experiment"], "check": "screen",
                                               "screen_changed": r["screen_changed"],
                                               "expected": screen_expected[r["name"]]})
            # Remember which experiments contradicted this address, so saving a discovery cannot cite them as support.
            for c in contradictions:
                stored = self._experiments.get(c["experiment"])
                if stored is not None:
                    marks = stored.setdefault("validation_contradicts", [])
                    if addr not in marks:
                        marks.append(addr)
            if contradictions:
                self._save_history()
            if contradictions:
                verdict = "contradicted"
            elif all(per_action[n]["runs"] >= 2 for n in expected):
                verdict = "supported_by_experiments"
            else:
                verdict = "inconclusive"
            return {
                "address": f"0x{addr:04X}", "checkpoint": checkpoint,
                "verdict": verdict, "verdict_meaning": {
                    "supported_by_experiments": "every expectation held in every run; still not verified",
                    "contradicted": "at least one run disagreed with the expectation",
                    "inconclusive": "fewer than 2 runs for an expected experiment",
                }[verdict],
                "per_action": per_action, "confirmations": len(confirmed_runs),
                "contradictions": contradictions[:50], "runs": runs,
                "frames_advanced_total": sum(r["advanced_frames"] for r in runs),
                "perturbation": perturbation,
                "limitations": [
                    "These are experiments from one checkpoint with the named inputs. They do not show what the "
                    "address means.",
                    "Repeat from other checkpoints, then record the discovery with investigate_save_discovery; "
                    "verified status needs cited experiments."],
            }


    def investigate_screen_changes(self, baseline: str, current: bool = True, target: str | None = None,
                                   mode: str = "both", threshold: int = 0, region: dict[str, int] | None = None,
                                   include_sprites: bool = True, include_tiles: bool = True,
                                   include_image: bool = False,
                                   max_items: int = 50) -> tuple[dict[str, Any], bytes | None]:
        """Visible change between two frames: pixels, 8x8 cells, bounding rectangles, sprites, and background tiles.

        Built on compare_screen and compare_tilemaps. Everything reported is a direct comparison of two stored
        frames. Object identities are only inferred, and are labelled that way.
        """
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 200:
            raise ValueError("max_items must be 1..200")
        if region is not None:
            region = self._check_screen_region(region)
        with self._lock:
            base, other = self._pair(baseline, current, target, screen=True)
            for snap in (base, other):
                if snap["screen"] is None:
                    raise ValueError(f"{snap['id']} was captured without a screen")
            frame, png = analysis.compare_screen(base["screen"], other["screen"], mode=mode, threshold=threshold,
                                                 region=region, include_image=include_image, max_items=max_items)
            out: dict[str, Any] = {
                "baseline": base["id"], "target": other["id"],
                "baseline_frame": base["frame"], "target_frame": other["frame"],
                "pixels": {"changed": frame["changed_pixels"], "percent_changed": frame["percent_changed"],
                           "examined": frame["pixels_examined"], "threshold": threshold, "region": frame["region"]},
                "bounding_box": frame.get("bounding_box"),
                "rectangles": frame.get("areas"), "rectangle_count": frame.get("area_count"),
                "cells": frame.get("grid"),
                "basis": "direct: computed from the two stored frames",
            }
            if not (base["screen_fresh"] and other["screen_fresh"]):
                out["warning"] = self.STALE_SCREEN
            if include_sprites:
                if base.get("oam") is not None and other.get("oam") is not None:
                    cgb = bool(self._header_cgb(self._pyboy).get("cgb_compatible"))
                    before = graphics.sprite_rows(base["oam"], base["lcdc"], cgb=cgb)
                    after = graphics.sprite_rows(other["oam"], other["lcdc"], cgb=cgb)
                    out["sprites"] = analysis.sprite_changes(before, after, max_items)
                else:
                    out["sprites"] = {"unavailable": "a snapshot has no sprite data (captured before OAM was stored)"}
            if include_tiles:
                if base["tilemaps"] is not None and other["tilemaps"] is not None:
                    out["background_tiles"] = analysis.compare_tilemaps(base["tilemaps"], other["tilemaps"], max_items)
                else:
                    out["background_tiles"] = {"unavailable": "capture both snapshots with include_tilemap=true"}
            moved = out.get("sprites", {}).get("moved_count", 0)
            tiles = sum(v.get("changed_tiles", 0) for v in out.get("background_tiles", {}).values()
                        if isinstance(v, dict))
            out["summary"] = (f"{frame['changed_pixels']} pixels changed in {frame.get('area_count', 0)} "
                              f"rectangle(s); {moved} sprite(s) moved; {tiles} background tile(s) changed")
            return out, png
