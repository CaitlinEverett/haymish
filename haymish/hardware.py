"""Hardware profiling and system-load gating for local AI work.

Two jobs:
  1. Size the caption worker pool to the actual machine. Local vision inference
     scales with GPU cores and unified memory, and the useful range is wide --
     an M4 Max sustains 12+ concurrent requests while an 8 GB M1 thrashes past
     2. Measured on an M4 Max: sequential captioning ran 5.7 s/photo, 4-way
     3.4 s/photo, 12-way 1.8 s/photo, still improving -- so the ceiling here is
     deliberately generous on big machines and conservative on small ones.
  2. Tell a scheduled job whether now is a good time to run. An unattended sweep
     that fights the user's foreground work for GPU is worse than one that waits.

Everything degrades safely: unknown hardware, non-Apple-Silicon, or a failed
sysctl all fall back to conservative defaults rather than raising.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

# Rough per-request unified-memory headroom for a small vision model (gemma3:4b
# is ~4.7 GB resident; concurrent slots share weights but each needs KV-cache
# and activation space). Deliberately pessimistic -- overshooting concurrency
# on a small machine causes swap, which is far worse than being one worker slow.
_GB_PER_WORKER = 3.0
_MAX_WORKERS = 12
_MIN_WORKERS = 1


@dataclass
class Hardware:
    chip: str = "unknown"
    apple_silicon: bool = False
    performance_cores: int = 0
    efficiency_cores: int = 0
    total_cores: int = 0
    gpu_cores: int = 0
    memory_gb: float = 0.0

    def describe(self) -> str:
        if not self.apple_silicon:
            return f"{self.chip} · {self.total_cores} cores · {self.memory_gb:.0f} GB"
        gpu = f" · {self.gpu_cores}-core GPU" if self.gpu_cores else ""
        return (f"{self.chip} · {self.performance_cores}P+{self.efficiency_cores}E"
                f"{gpu} · {self.memory_gb:.0f} GB")


def _sysctl(key: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def _int_sysctl(key: str) -> int:
    raw = _sysctl(key)
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def _gpu_cores() -> int:
    """Apple doesn't expose GPU core count via sysctl; system_profiler does, but
    it's slow (~1s), so this is only called from detect() which callers cache."""
    if not shutil.which("system_profiler"):
        return 0
    try:
        out = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in out.stdout.splitlines():
        if "Total Number of Cores" in line:
            try:
                return int(line.split(":")[1].strip().split()[0])
            except (IndexError, ValueError):
                return 0
    return 0


def detect() -> Hardware:
    chip = _sysctl("machdep.cpu.brand_string") or "unknown"
    apple_silicon = chip.startswith("Apple")
    memory_bytes = _int_sysctl("hw.memsize")
    hw = Hardware(
        chip=chip,
        apple_silicon=apple_silicon,
        performance_cores=_int_sysctl("hw.perflevel0.physicalcpu"),
        efficiency_cores=_int_sysctl("hw.perflevel1.physicalcpu"),
        total_cores=_int_sysctl("hw.physicalcpu"),
        memory_gb=memory_bytes / (1024 ** 3) if memory_bytes else 0.0,
    )
    if apple_silicon:
        hw.gpu_cores = _gpu_cores()
    return hw


def recommended_caption_workers(hw: Hardware | None = None) -> int:
    """How many caption requests to keep in flight.

    Bounded by three things: unified memory (the hard constraint -- exceeding it
    means swapping), GPU cores (the throughput constraint), and a fixed ceiling
    past which Ollama's own queueing dominates anyway.
    """
    hw = hw or detect()
    if not hw.apple_silicon:
        # Intel Macs run these models on CPU; parallelism mostly just thrashes.
        return 2 if hw.memory_gb >= 32 else 1

    by_memory = int(hw.memory_gb // _GB_PER_WORKER) if hw.memory_gb else 2
    # GPU cores are the throughput ceiling; ~4 cores per concurrent request is
    # where the M-series scaling measured above starts flattening.
    by_gpu = max(1, hw.gpu_cores // 4) if hw.gpu_cores else 4
    workers = min(by_memory, by_gpu, _MAX_WORKERS)
    return max(_MIN_WORKERS, workers)


# -- system load gating -------------------------------------------------------

@dataclass
class LoadState:
    load_average: float = 0.0
    cores: int = 1
    memory_free_percent: float | None = None
    on_battery: bool = False

    @property
    def load_per_core(self) -> float:
        return self.load_average / max(1, self.cores)


def _memory_free_percent() -> float | None:
    if not shutil.which("memory_pressure"):
        return None
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        if "free percentage" in line.lower():
            try:
                return float(line.rsplit(":", 1)[1].strip().rstrip("%"))
            except (IndexError, ValueError):
                return None
    return None


def _on_battery() -> bool:
    if not shutil.which("pmset"):
        return False
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return "Battery Power" in out.stdout


def load_state() -> LoadState:
    raw = _sysctl("vm.loadavg") or ""
    load = 0.0
    for token in raw.replace("{", " ").replace("}", " ").split():
        try:
            load = float(token)
            break
        except ValueError:
            continue
    return LoadState(
        load_average=load,
        cores=_int_sysctl("hw.physicalcpu") or os.cpu_count() or 1,
        memory_free_percent=_memory_free_percent(),
        on_battery=_on_battery(),
    )


def busy_reason(max_load_per_core: float = 0.7, min_memory_free_percent: float = 20.0,
                 skip_on_battery: bool = True) -> str | None:
    """Why now is a bad time for heavy unattended work, or None if it's fine.

    Used by scheduled runs so an overnight index doesn't fight foreground work
    (or drain a laptop on battery). Interactive runs never call this -- if a
    person typed the command, they've decided.
    """
    state = load_state()
    if skip_on_battery and state.on_battery:
        return "running on battery — deferring to avoid draining it"
    if state.load_per_core > max_load_per_core:
        return (f"system busy (load {state.load_average:.1f} across {state.cores} cores "
                f"= {state.load_per_core:.1f}/core, threshold {max_load_per_core})")
    if (state.memory_free_percent is not None
            and state.memory_free_percent < min_memory_free_percent):
        return (f"low free memory ({state.memory_free_percent:.0f}%, "
                f"threshold {min_memory_free_percent:.0f}%)")
    return None
