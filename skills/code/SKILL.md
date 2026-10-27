---
name: code
description: Find the Game Boy code behind a behaviour - read the cartridge header, the ROM bytes and the disassembly, inspect CPU registers, and find which code runs at an address using execution hooks. Use when the user asks how the game implements something, what a routine does, or what code writes or reads a value.
---

# Find the code behind a behaviour

Goal: connect a behaviour or a memory value to the ROM code that produces it, with evidence from the ROM and the running CPU.

Code analysis is read-only. `inspect_code` never changes the ROM or the emulator.

## Tools

- `inspect_code(kind='cartridge')`: header facts (title, cartridge type, ROM and RAM size, CGB flag, SHA-1), and integrity checks. Check `all_checks_pass` first.
- `inspect_code(kind='rom', bank=..., address=..., length=...)`: raw bytes from the ROM file. Bank 0 is 0x0000-0x3FFF; banks 1 and up are 0x4000-0x7FFF. `cross_check` compares them with the emulator's view of the same bank.
- `inspect_code(kind='disasm', bank=..., address=..., count=...)`: LR35902 instructions from the ROM file, with jump and call targets. This is an interpretation of bytes, not proof that they run.
- `inspect_code(kind='cpu')`: registers, register pairs and flags at the current frame. Interrupt state and the current ROM bank are not exposed by PyBoy's public API.
- `hook_execution(action='register', bank=..., address=..., label=...)`: count instruction fetches at one address and keep register snapshots. Needs `DMG_CARTOGRAPHER_ENABLE_DEBUG=1`. `list` and `remove` manage hooks. Hooks are cleared on game reset or load.
- `memory(action='read', ...)` and `search_memory`: find the RAM bytes first, then see what code touches them.

## Workflow

1. `inspect_code(kind='cartridge')` for the header and checks. Confirm the file and the emulator agree.
2. Find where the behaviour happens in time. Use `step` from a checkpoint to reach the moment the value changes (see the `find-variable` skill for finding the value).
3. Read the CPU at that moment: `inspect_code(kind='cpu')`. The program counter (PC) points at code running now.
4. Disassemble around the PC: `inspect_code(kind='disasm', bank=..., address=<PC or a nearby label>, count=32)`. Bank comes from where the PC lives (0x4000-0x7FFF needs the switchable bank; `inspect_code(kind='rom')` with `cross_check` helps confirm it). Follow jump and call targets by disassembling them.
5. To find what writes a value, register a hook at a candidate routine: `hook_execution(action='register', bank=..., address=..., label='routine_x')`, step, then `hook_execution(action='list')` to read its hit count and register snapshots. A hook says the routine ran; it doesn't say it wrote the value.
6. Record the link with `discoveries(action='save', ...)`, citing the experiments or replays that show it.

## Pitfalls

- A hook counts instruction fetches at one address. It doesn't watch data. Pair it with `watch(addresses=[...])` to see when a value changes.
- Hook callbacks see no frame numbers. Use `watch` for frame timing.
- Disassembly is linear. Data mixed into code, or jump tables, will decode as nonsense. Check `invalid` and check that targets land on sensible boundaries.
- The CPU state is a single snapshot. Get several frames to see a routine's full effect.
- Bank numbers matter. The same address in bank 1 and bank 2 is different code.
- Don't rely on RAM addresses from another game; the code layout differs too.
