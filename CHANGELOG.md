# Changelog

## v0.2 (2026-11-22)

- **Intel Arc Pro B60 24 GB** added (2026-11-21) as a third card, on PCIe 4.0 x8. It runs a second llama.cpp
  router on port 9704 with the SYCL build b12613: fast-qwen (Qwen3.6 35B-A3B) at start, Qwen3.8 27B as
  code reviewer with its built-in MTP draft.
- `docs/HARDWARE.md`: B60 in the parts, PCIe and power tables, its Level Zero numbering, software, and
  quirks (Gemma 4 crashes past 10-12K tokens, `libumf` needed, no flash attention).
- `BENCHMARKS.md`: B60 speed, MTP, batch sizes, and a 25-sample code review test.
- `recipes/`: `router-b60.ini`, `start-b60.sh`, `systemd/locai-b60.service`, and setup steps.
- `docs/LESSONS.md`: drafts built into model files, verdict-only reviews, long-context tests on a new backend.
- `bench/review_eval.py`, `bench/review_test.py` and `bench/review_samples.json`: the code review test, with
  its 25 samples; `--check` re-runs them against the hidden checks.
- `scripts/report.sh`: lists Intel cards on the `xe` driver too (VRAM, power limit, real PCIe link), the
  oneAPI runtime and the SYCL llama.cpp build.
- README: how to run the tests on Ubuntu, which blocks the sandbox's user namespaces by default.
- BIOS: still on factory defaults after adding the B60.

Not yet: the coding sets on the B60.

## v0.1 (2026-11-08)

First version.

- README with the build, photos and a results summary.
- `docs/HARDWARE.md`: parts, PCIe layout (both GPUs PCIe 4.0 x16 to the CPU), memory bandwidth, power,
  quirks (9060 XT SMU hang at boot, reversed HIP order).
- `BENCHMARKS.md`: coding hard and basic sets for 15 model configurations, speed and VRAM, DFlash draft
  sizes, R9700 power limit 250/285/300 W, 9060 XT services. Measured 2026-10-23 to 2026-11-03.
- `recipes/`: llama.cpp router presets, start scripts, systemd units, R9700 power-cap service.
- `bench/code_test.py`: the coding benchmark.
- `scripts/report.sh`: rig report for AMD GPU machines.
- BIOS: factory defaults, nothing changed; the state in effect as seen from Linux (UEFI, Resizable BAR,
  IOMMU, ASPM, SMT, NPS1, boost, C-states).
- Benchmark checks validated against the reference solutions.
- `docs/LESSONS.md`: lessons learned building and testing the server.

Not yet: Qwen3.5 9B speed on the 9060 XT, results from other machines; the system monitor (HUD),
certificates, firewall ports and Docker setup.
