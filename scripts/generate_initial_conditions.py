#!/usr/bin/env python3
"""Generate a Cartesian sweep of hydrostatic spheres_ini collision setups."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import string
import subprocess
import sys
from typing import Any, Iterable


G_SI = 6.6741e-11
SPHERES_INI_OUTPUT_MODE = 0
SPHERES_INI_SOLID_OUTPUT_MODE = 1
SPHERES_INI_FRAGMENTATION_OUTPUT_MODE = 2
SPHERES_INI_NO_DENSITY_OUTPUT_MODE = 3
GENERATION_DEFAULTS = {
    "mode": "hydro",
    "fragmentation": False,
    "density_column": True,
}
MILUPHCUDA_SCRIPT_NAME = "run_miluphcuda.sh"
MANIFEST_SCHEMA_VERSION = 2
INCOMPLETE_ATTEMPTS_DIRECTORY = "_incomplete_attempts"
CASE_STATUSES = {"pending", "running", "interrupted", "failed", "complete"}
PARAMETER_NAMES = (
    "m_tot_kg",
    "gamma",
    "zeta_iron",
    "v_imp_over_v_esc",
    "impact_angle_deg",
    "f_i",
    "f_t",
    "n_tot",
)
MILUPHCUDA_PLACEHOLDERS = {
    "case_directory",
    "impact_file",
    "material_file",
    "simulation_end_time_s",
    "n_frames",
    "output_interval_s",
}


class ConfigurationError(ValueError):
    """Raised when a sweep configuration is invalid."""


class SweepTermination(Exception):
    """Raised when the process receives a termination signal."""

    def __init__(self, signum: int):
        super().__init__(f"received signal {signum}")
        self.signum = signum


class CaseInterrupted(Exception):
    """Carries captured child output when a case is interrupted."""

    def __init__(self, cause: BaseException, stdout: str, stderr: str):
        super().__init__(str(cause))
        self.cause = cause
        self.stdout = stdout
        self.stderr = stderr


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{context} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ConfigurationError(f"{context} must be finite")
    return value


def expand_parameter(name: str, specification: Any) -> list[float]:
    if not isinstance(specification, dict):
        raise ConfigurationError(f"parameters.{name} must be an object")
    mode = specification.get("mode")
    context = f"parameters.{name}"

    if mode == "constant":
        values = [_number(specification.get("value"), f"{context}.value")]
    elif mode == "list":
        raw_values = specification.get("values")
        if not isinstance(raw_values, list) or not raw_values:
            raise ConfigurationError(f"{context}.values must be a non-empty list")
        values = [_number(value, f"{context}.values") for value in raw_values]
    elif mode in {"linear", "log"}:
        minimum = _number(specification.get("minimum"), f"{context}.minimum")
        maximum = _number(specification.get("maximum"), f"{context}.maximum")
        count_raw = specification.get("count")
        if isinstance(count_raw, bool) or not isinstance(count_raw, int) or count_raw < 1:
            raise ConfigurationError(f"{context}.count must be a positive integer")
        if maximum < minimum:
            raise ConfigurationError(f"{context}.maximum must be >= minimum")
        if mode == "log" and minimum <= 0:
            raise ConfigurationError(f"{context}.minimum must be > 0 for a log grid")
        if count_raw == 1:
            if minimum != maximum:
                raise ConfigurationError(
                    f"{context} with count 1 requires equal minimum and maximum"
                )
            values = [minimum]
        elif mode == "linear":
            step = (maximum - minimum) / (count_raw - 1)
            values = [minimum + step * index for index in range(count_raw)]
        else:
            log_minimum = math.log(minimum)
            log_step = (math.log(maximum) - log_minimum) / (count_raw - 1)
            values = [math.exp(log_minimum + log_step * index) for index in range(count_raw)]
            values[0], values[-1] = minimum, maximum
    else:
        raise ConfigurationError(
            f"{context}.mode must be one of constant, list, linear, or log"
        )

    if len(set(values)) != len(values):
        raise ConfigurationError(f"{context} expands to duplicate values")
    return values


def validate_case(case: dict[str, float]) -> None:
    if case["m_tot_kg"] <= 0:
        raise ConfigurationError("m_tot_kg must be > 0")
    if not 0 < case["gamma"] <= 1:
        raise ConfigurationError("gamma must be in (0, 1]")
    if not 0 < case["zeta_iron"] < 1:
        raise ConfigurationError("zeta_iron must be in (0, 1)")
    if case["v_imp_over_v_esc"] <= 0:
        raise ConfigurationError("v_imp_over_v_esc must be > 0")
    if not 0 <= case["impact_angle_deg"] <= 90:
        raise ConfigurationError("impact_angle_deg must be in [0, 90]")
    if case["f_i"] < 1:
        raise ConfigurationError("f_i must be >= 1")
    if case["f_t"] != 50:
        raise ConfigurationError("f_t is fixed at 50")
    if not case["n_tot"].is_integer() or case["n_tot"] < 100:
        raise ConfigurationError("n_tot must be an integer >= 100")


def expand_cases(parameters: Any) -> list[dict[str, float]]:
    if not isinstance(parameters, dict):
        raise ConfigurationError("parameters must be an object")
    missing = set(PARAMETER_NAMES) - set(parameters)
    extra = set(parameters) - set(PARAMETER_NAMES)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ConfigurationError("invalid parameters: " + "; ".join(details))

    grids = [expand_parameter(name, parameters[name]) for name in PARAMETER_NAMES]
    cases = []
    for combination in itertools.product(*grids):
        case = dict(zip(PARAMETER_NAMES, combination, strict=True))
        validate_case(case)
        cases.append(case)
    return cases


def _spheres_ini_output_mode(generation: dict[str, Any]) -> int:
    if generation["mode"] == "solid":
        return (
            SPHERES_INI_FRAGMENTATION_OUTPUT_MODE
            if generation["fragmentation"]
            else SPHERES_INI_SOLID_OUTPUT_MODE
        )
    return (
        SPHERES_INI_OUTPUT_MODE
        if generation["density_column"]
        else SPHERES_INI_NO_DENSITY_OUTPUT_MODE
    )


def render_spheres_input(
    case: dict[str, float], spheres_ini_output_mode: int = SPHERES_INI_OUTPUT_MODE
) -> str:
    projectile_mass = case["m_tot_kg"] * case["gamma"] / (1 + case["gamma"])
    mantle_fraction = 1 - case["zeta_iron"]
    values = {
        "N": int(case["n_tot"]),
        "M_tot": case["m_tot_kg"],
        "M_proj": projectile_mass,
        "vel_vesc": case["v_imp_over_v_esc"],
        "impact_angle": case["impact_angle_deg"],
        "ini_dist_fact": case["f_i"],
        "mantle_proj": mantle_fraction,
        "shell_proj": 0,
        "mantle_target": mantle_fraction,
        "shell_target": 0,
        "core_mat": "Iron",
        "mantle_mat": "BasaltNakamura",
        "shell_mat": "BasaltNakamura",
        "core_eos": "T",
        "mantle_eos": "T",
        "shell_eos": "T",
        "weibull_core": 0,
        "weibull_mantle": int(
            spheres_ini_output_mode == SPHERES_INI_FRAGMENTATION_OUTPUT_MODE
        ),
        "weibull_shell": 0,
        "proj_rot_period": -1,
        "targ_rot_period": -1,
        "proj_rot_axis_x": 0,
        "proj_rot_axis_y": 0,
        "proj_rot_axis_z": 1,
        "targ_rot_axis_x": 0,
        "targ_rot_axis_y": 0,
        "targ_rot_axis_z": 1,
    }
    mechanics_description = (
        "basalt-mantle Weibull flaws enabled"
        if spheres_ini_output_mode == SPHERES_INI_FRAGMENTATION_OUTPUT_MODE
        else "Weibull flaws disabled"
    )
    lines = [
        "# Generated by scripts/generate_initial_conditions.py.",
        "# Hydrostatic iron-core/basalt-mantle bodies; no shell or spin; "
        f"{mechanics_description}.",
    ]
    lines.extend(
        f"{name} = {value:.16g}" if isinstance(value, float) else f"{name} = {value}"
        for name, value in values.items()
    )
    return "\n".join(lines) + "\n"


def _case_id(case: dict[str, float]) -> str:
    canonical = json.dumps(case, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:10]


def _resolve_path(config_path: Path, raw_path: Any, context: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ConfigurationError(f"{context} must be a non-empty path string")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def load_configuration(config_path: Path) -> tuple[dict[str, Any], list[dict[str, float]]]:
    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"cannot read {config_path}: {error}") from error
    if not isinstance(config, dict):
        raise ConfigurationError("the JSON root must be an object")
    required_sections = {"paths", "execution", "parameters"}
    extra_sections = set(config) - required_sections - {"generation"}
    missing_sections = required_sections - set(config)
    if missing_sections or extra_sections:
        raise ConfigurationError(
            "the JSON root must contain paths, execution, and parameters, with "
            "generation as the only optional section"
        )

    generation = config.get("generation", GENERATION_DEFAULTS.copy())
    if not isinstance(generation, dict) or set(generation) != set(GENERATION_DEFAULTS):
        raise ConfigurationError(
            "generation must contain exactly mode, fragmentation, and density_column"
        )
    if generation["mode"] not in {"hydro", "solid"}:
        raise ConfigurationError("generation.mode must be hydro or solid")
    if not isinstance(generation["fragmentation"], bool):
        raise ConfigurationError("generation.fragmentation must be a boolean")
    if not isinstance(generation["density_column"], bool):
        raise ConfigurationError("generation.density_column must be a boolean")
    if generation["mode"] == "hydro" and generation["fragmentation"]:
        raise ConfigurationError("generation.fragmentation requires solid mode")
    if generation["mode"] == "solid" and not generation["density_column"]:
        raise ConfigurationError("solid output requires the density column")
    config["generation"] = generation

    paths = config["paths"]
    if not isinstance(paths, dict) or set(paths) != {
        "spheres_ini_executable", "spheres_ini_source", "material_file", "output_directory"
    }:
        raise ConfigurationError(
            "paths must contain exactly spheres_ini_executable, spheres_ini_source, "
            "material_file, and output_directory"
        )
    config["resolved_paths"] = {
        key: _resolve_path(config_path, value, f"paths.{key}") for key, value in paths.items()
    }

    execution = config["execution"]
    if not isinstance(execution, dict) or set(execution) != {"max_cases", "miluphcuda"}:
        raise ConfigurationError("execution must contain exactly max_cases and miluphcuda")
    max_cases = execution["max_cases"]
    if isinstance(max_cases, bool) or not isinstance(max_cases, int) or max_cases < 1:
        raise ConfigurationError("execution.max_cases must be a positive integer")
    _validate_miluphcuda_config(execution["miluphcuda"])

    cases = expand_cases(config["parameters"])
    if len(cases) > max_cases:
        raise ConfigurationError(
            f"sweep expands to {len(cases)} cases, exceeding max_cases={max_cases}"
        )
    return config, cases


def _validate_miluphcuda_config(configuration: Any) -> None:
    if not isinstance(configuration, dict) or set(configuration) != {
        "enabled", "executable", "arguments", "n_frames"
    }:
        raise ConfigurationError(
            "execution.miluphcuda must contain exactly enabled, executable, arguments, "
            "and n_frames"
        )
    if configuration["enabled"] is not False:
        raise ConfigurationError(
            "execution.miluphcuda.enabled must remain false; this generator only plans the command"
        )
    if not isinstance(configuration["executable"], str) or not configuration["executable"]:
        raise ConfigurationError("execution.miluphcuda.executable must be a non-empty string")
    arguments = configuration["arguments"]
    if not isinstance(arguments, list) or not all(
        isinstance(argument, str) for argument in arguments
    ):
        raise ConfigurationError("execution.miluphcuda.arguments must be a list of strings")
    formatter = string.Formatter()
    for argument in arguments:
        try:
            parsed = formatter.parse(argument)
            for _, field_name, format_spec, conversion in parsed:
                if field_name is None:
                    continue
                if field_name not in MILUPHCUDA_PLACEHOLDERS:
                    raise ConfigurationError(
                        f"unknown miluphcuda argument placeholder: {field_name}"
                    )
                if format_spec or conversion:
                    raise ConfigurationError(
                        "miluphcuda argument placeholders do not accept conversions or format specs"
                    )
        except ValueError as error:
            raise ConfigurationError(
                f"invalid execution.miluphcuda argument template: {error}"
            ) from error
    n_frames = configuration["n_frames"]
    if isinstance(n_frames, bool) or not isinstance(n_frames, int) or n_frames < 1:
        raise ConfigurationError("execution.miluphcuda.n_frames must be a positive integer")


def _validate_runtime_paths(paths: dict[str, Path]) -> None:
    executable = paths["spheres_ini_executable"]
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ConfigurationError(f"spheres_ini executable is missing or not executable: {executable}")
    source = paths["spheres_ini_source"]
    if not (source / "run_SEAGen.py").is_file() or not (source / "SEAGen" / "seagen.py").is_file():
        raise ConfigurationError(f"spheres_ini source lacks the installed SEAGen wrapper/dependency: {source}")
    material = paths["material_file"]
    if not material.is_file():
        raise ConfigurationError(f"material file does not exist: {material}")


def _write_json_atomic(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _write_text_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actual_counts(stdout: str) -> tuple[int, int]:
    counts = []
    for body in ("projectile", "target"):
        match = re.search(rf"^\s*{body}:\s+N_des\s*=\s*\d+\s+N\s*=\s*(\d+)", stdout, re.MULTILINE)
        if not match:
            raise RuntimeError(f"could not parse actual {body} particle count")
        counts.append(int(match.group(1)))
    return counts[0], counts[1]


def _actual_radii(stdout: str) -> tuple[float, float]:
    radii = []
    for body in ("projectile", "target"):
        match = re.search(
            rf"^\s*{body}:\s+desired:\s+R\s*=.*\n\s*actual/final:\s+R\s*=\s*([0-9.eE+-]+)",
            stdout,
            re.MULTILINE,
        )
        if not match:
            raise RuntimeError(f"could not parse actual {body} radius")
        radii.append(float(match.group(1)))
    return radii[0], radii[1]


def _collision_timescale(stdout: str) -> float:
    match = re.search(
        r"collision timescale \(R_p\+R_t\)/\|v_imp\|\s*=\s*([0-9.eE+-]+)\s*sec",
        stdout,
    )
    if not match:
        raise RuntimeError("could not parse spheres_ini collision timescale")
    timescale = float(match.group(1))
    if not math.isfinite(timescale) or timescale <= 0:
        raise RuntimeError("spheres_ini reported an invalid collision timescale")
    return timescale


def _outer_radius(path: Path) -> float:
    radius = None
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            radius = float(line.split()[0])
    if radius is None or radius <= 0:
        raise RuntimeError(f"could not read an outer radius from {path}")
    return radius


def _particle_mass(path: Path) -> tuple[int, float]:
    count = 0
    mass = 0.0
    with path.open() as particle_file:
        for line in particle_file:
            if not line.strip() or line.startswith("#"):
                continue
            columns = line.split()
            if len(columns) < 7:
                raise RuntimeError(f"particle row has fewer than seven columns in {path}")
            mass += float(columns[6])
            count += 1
    return count, mass


def _derived_metadata(case: dict[str, float], case_directory: Path, stdout: str) -> dict[str, Any]:
    projectile_count, target_count = _actual_counts(stdout)
    projectile_radius, target_radius = _actual_radii(stdout)
    file_count, actual_mass = _particle_mass(case_directory / "impact.0000")
    if file_count != projectile_count + target_count:
        raise RuntimeError("particle output row count does not match spheres_ini summary")
    projectile_structure_radius = _outer_radius(case_directory / "projectile.structure")
    target_structure_radius = _outer_radius(case_directory / "target.structure")
    combined_radius = projectile_radius + target_radius
    mutual_escape_velocity = math.sqrt(2 * G_SI * actual_mass / combined_radius)
    impact_velocity = case["v_imp_over_v_esc"] * mutual_escape_velocity
    collision_timescale_recomputed = combined_radius / impact_velocity
    collision_timescale = _collision_timescale(stdout)
    return {
        "n_projectile_actual": projectile_count,
        "n_target_actual": target_count,
        "n_tot_actual": file_count,
        "m_tot_actual_kg": actual_mass,
        "projectile_radius_m": projectile_radius,
        "target_radius_m": target_radius,
        "projectile_hydrostatic_profile_radius_m": projectile_structure_radius,
        "target_hydrostatic_profile_radius_m": target_structure_radius,
        "mutual_escape_velocity_m_per_s": mutual_escape_velocity,
        "impact_velocity_m_per_s": impact_velocity,
        "collision_timescale_s": collision_timescale,
        "collision_timescale_source": "spheres_ini stdout log",
        "collision_timescale_recomputed_s": collision_timescale_recomputed,
        "simulation_end_time_s": (case["f_i"] + case["f_t"]) * collision_timescale,
    }


def _planned_miluphcuda(
    configuration: dict[str, Any], simulation_end_time: float
) -> dict[str, Any]:
    n_frames = configuration["n_frames"]
    substitutions = {
        "case_directory": ".",
        "impact_file": "impact.0000",
        "material_file": "material.cfg",
        "simulation_end_time_s": format(simulation_end_time, ".16g"),
        "n_frames": str(n_frames),
        "output_interval_s": format(simulation_end_time / n_frames, ".16g"),
    }
    try:
        arguments = [
            argument.format_map(substitutions) for argument in configuration["arguments"]
        ]
    except (KeyError, ValueError) as error:
        raise ConfigurationError(
            f"invalid placeholder in execution.miluphcuda.arguments: {error}"
        ) from error
    command = [configuration["executable"], *arguments]
    return {
        "status": "planned_not_executed",
        "enabled": False,
        "n_frames": n_frames,
        "working_directory": ".",
        "script_file": MILUPHCUDA_SCRIPT_NAME,
        "command": command,
        "command_text": shlex.join(command),
    }


def _write_miluphcuda_script(
    case_directory: Path, planned_command: dict[str, Any]
) -> Path:
    script_path = case_directory / MILUPHCUDA_SCRIPT_NAME
    script_path.write_text(
        "#!/bin/sh\n"
        f'cd "$(dirname "$0")" && exec {planned_command["command_text"]}\n'
    )
    script_path.chmod(0o755)
    return script_path


def _case_records(cases: Iterable[dict[str, float]]) -> list[dict[str, Any]]:
    records = []
    case_ids = set()
    for index, case in enumerate(cases, start=1):
        case_id = _case_id(case)
        if case_id in case_ids:
            raise ConfigurationError(
                "two cases have the same shortened identity; adjust the parameter grid"
            )
        case_ids.add(case_id)
        case_name = f"case_{index:05d}_{case_id}"
        records.append(
            {
                "case_id": case_id,
                "case_name": case_name,
                "parameters": case,
                "status": "pending",
                "attempts": 0,
            }
        )
    return records


def render_case_table(records: Iterable[dict[str, Any]]) -> str:
    headers = (
        "status",
        "attempts",
        "case_directory",
        *PARAMETER_NAMES,
        "n_tot_actual",
    )
    rows = []
    for record in records:
        parameters = record["parameters"]
        values = [
            record.get("status", "pending"),
            str(record.get("attempts", 0)),
            record["case_name"],
        ]
        for name in PARAMETER_NAMES:
            value = parameters[name]
            values.append(
                str(int(value)) if name == "n_tot" else format(value, ".16g")
            )
        actual_count = record.get("derived", {}).get("n_tot_actual")
        values.append(str(actual_count) if type(actual_count) is int else "-")
        rows.append(values)

    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]

    def render_row(values: Iterable[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        ).rstrip()

    separator = "-+-".join("-" * width for width in widths)
    return "\n".join([render_row(headers), separator, *(render_row(row) for row in rows)]) + "\n"


def _restart_signature(
    config: dict[str, Any], spheres_ini_output_mode: int
) -> dict[str, Any]:
    return {
        "material_sha256": _sha256_file(config["resolved_paths"]["material_file"]),
        "miluphcuda": config["execution"]["miluphcuda"],
        "spheres_ini": {
            "hydrostatic_structure": True,
            "particle_geometry": 2,
            "output_mode": spheres_ini_output_mode,
        },
    }


def _plan_fingerprint(records: Iterable[dict[str, Any]]) -> str:
    case_ids = sorted(record["case_id"] for record in records)
    return hashlib.sha256(json.dumps(case_ids, separators=(",", ":")).encode()).hexdigest()


def _new_manifest(
    config_path: Path,
    config: dict[str, Any],
    records: list[dict[str, Any]],
    spheres_ini_output_mode: int,
) -> dict[str, Any]:
    density_column = spheres_ini_output_mode != SPHERES_INI_NO_DENSITY_OUTPUT_MODE
    output_descriptions = {
        SPHERES_INI_OUTPUT_MODE: "miluphcuda hydro with density column",
        SPHERES_INI_NO_DENSITY_OUTPUT_MODE: "miluphcuda hydro without density column",
        SPHERES_INI_SOLID_OUTPUT_MODE: "miluphcuda solid without fragmentation",
        SPHERES_INI_FRAGMENTATION_OUTPUT_MODE: "miluphcuda solid with fragmentation",
    }
    solid_mechanics = config["generation"]["mode"] == "solid"
    fragmentation_damage = config["generation"]["fragmentation"]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "config_file": str(config_path),
        "generator": str(Path(__file__).resolve()),
        "generation": config["generation"],
        "restart_signature": _restart_signature(config, spheres_ini_output_mode),
        "plan_fingerprint": _plan_fingerprint(records),
        "mode": {
            "hydrostatic_structure": True,
            "particle_geometry": "SEAGen spherical shells",
            "output": output_descriptions[spheres_ini_output_mode],
            "spheres_ini_output_mode": spheres_ini_output_mode,
            "density_column": density_column,
            "solid_mechanics": solid_mechanics,
            "fragmentation_damage": fragmentation_damage,
        },
        "miluphcuda": {
            "status": "planning_only",
            "case_script_file": MILUPHCUDA_SCRIPT_NAME,
            **config["execution"]["miluphcuda"],
        },
        "case_count": len(records),
        "case_table_file": "case_table.txt",
        "extensions": [],
        "cases": records,
    }


def _persist_sweep_state(output_directory: Path, manifest: dict[str, Any]) -> None:
    status_counts: dict[str, int] = {}
    for record in manifest["cases"]:
        status = record["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    manifest["case_count"] = len(manifest["cases"])
    manifest["status_counts"] = status_counts
    manifest["updated_at"] = _utc_now()
    _write_text_atomic(
        output_directory / manifest["case_table_file"],
        render_case_table(manifest["cases"]),
    )
    _write_json_atomic(output_directory / "manifest.json", manifest)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"cannot read restart manifest {path}: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ConfigurationError(f"invalid restart manifest: {path}")
    return manifest


def _migrate_legacy_manifest(
    manifest: dict[str, Any],
    desired_records: list[dict[str, Any]],
    output_directory: Path,
    config: dict[str, Any],
    spheres_ini_output_mode: int,
) -> dict[str, Any]:
    density_column = spheres_ini_output_mode != SPHERES_INI_NO_DENSITY_OUTPUT_MODE
    legacy_mode = manifest.get("mode", {})
    if (
        legacy_mode.get("hydrostatic_structure") is not True
        or legacy_mode.get("spheres_ini_output_mode") != spheres_ini_output_mode
        or legacy_mode.get("density_column") is not density_column
    ):
        raise ConfigurationError(
            "legacy sweep generation mode is incompatible with the requested output format"
        )
    expected_miluphcuda = config["execution"]["miluphcuda"]
    legacy_miluphcuda = manifest.get("miluphcuda", {})
    if any(
        legacy_miluphcuda.get(key) != value
        for key, value in expected_miluphcuda.items()
    ):
        raise ConfigurationError(
            "legacy sweep planned miluphcuda configuration does not match the restart JSON"
        )

    table_path = output_directory / manifest.get("case_table_file", "case_table.txt")
    try:
        table_lines = table_path.read_text().splitlines()
        headers = [column.strip() for column in table_lines[0].split("|")]
        case_name_column = headers.index("case_directory")
        legacy_case_names = [
            line.split("|")[case_name_column].strip()
            for line in table_lines[2:]
            if line.strip()
        ]
    except (OSError, IndexError, ValueError) as error:
        raise ConfigurationError(
            "cannot migrate the legacy manifest without its complete case table"
        ) from error
    if manifest.get("case_count") != len(legacy_case_names):
        raise ConfigurationError("legacy manifest and case table have different case counts")

    legacy_names_by_id = {}
    for case_name in legacy_case_names:
        match = re.fullmatch(r"case_\d{5}_([0-9a-f]{10})", case_name)
        if not match or match.group(1) in legacy_names_by_id:
            raise ConfigurationError("legacy case table contains invalid case identities")
        legacy_names_by_id[match.group(1)] = case_name

    existing_by_id = {}
    for existing in manifest["cases"]:
        parameters = existing.get("parameters")
        if not isinstance(parameters, dict):
            raise ConfigurationError("legacy manifest contains a case without parameters")
        existing_by_id[_case_id(parameters)] = existing
    desired_by_id = {record["case_id"]: record for record in desired_records}
    if not set(legacy_names_by_id).issubset(desired_by_id):
        raise ConfigurationError("restart configuration omits cases from the legacy case table")
    migrated = []
    for case_id, case_name in legacy_names_by_id.items():
        desired = desired_by_id[case_id]
        existing = existing_by_id.get(case_id)
        if existing is None:
            migrated.append({**desired, "case_name": case_name})
            continue
        migrated.append(
            {
                **desired,
                **existing,
                "case_id": case_id,
                "case_name": case_name,
                "parameters": desired["parameters"],
                "attempts": existing.get("attempts", 1),
            }
        )
    manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    manifest["cases"] = migrated
    manifest.setdefault("created_at", _utc_now())
    manifest.setdefault("extensions", [])
    manifest["migrated_from_legacy_manifest_at"] = _utc_now()
    return manifest


def _prepare_restart_manifest(
    output_directory: Path,
    config_path: Path,
    config: dict[str, Any],
    desired_records: list[dict[str, Any]],
    restart_mode: str,
    spheres_ini_output_mode: int,
) -> dict[str, Any]:
    manifest_path = output_directory / "manifest.json"
    if not output_directory.is_dir() or not manifest_path.is_file():
        raise ConfigurationError(
            f"{restart_mode} requires an existing sweep directory with manifest.json: "
            f"{output_directory}"
        )
    manifest = _load_manifest(manifest_path)
    schema_version = manifest.get("schema_version")
    if schema_version is None:
        manifest = _migrate_legacy_manifest(
            manifest,
            desired_records,
            output_directory,
            config,
            spheres_ini_output_mode,
        )
        manifest["restart_signature"] = _restart_signature(
            config, spheres_ini_output_mode
        )
    elif schema_version != MANIFEST_SCHEMA_VERSION:
        raise ConfigurationError(
            f"unsupported restart manifest schema version: {schema_version}"
        )

    if manifest.get("restart_signature") != _restart_signature(
        config, spheres_ini_output_mode
    ):
        raise ConfigurationError(
            "restart configuration is incompatible with the stored material, output mode, "
            "or planned miluphcuda command"
        )

    stored_records = manifest["cases"]
    stored_by_id = {}
    stored_names = set()
    for record in stored_records:
        if not isinstance(record, dict) or not isinstance(record.get("parameters"), dict):
            raise ConfigurationError("restart manifest contains an invalid case record")
        calculated_id = _case_id(record["parameters"])
        case_id = record.get("case_id", calculated_id)
        case_name = record.get("case_name")
        if case_id != calculated_id:
            raise ConfigurationError("restart manifest contains a mismatched case identity")
        if not isinstance(case_name, str) or not re.fullmatch(
            rf"case_\d{{5}}_{case_id}", case_name
        ):
            raise ConfigurationError("restart manifest contains an invalid case directory name")
        if record.get("status") not in CASE_STATUSES:
            raise ConfigurationError("restart manifest contains an invalid case status")
        if case_id in stored_by_id or case_name in stored_names:
            raise ConfigurationError("restart manifest contains duplicate cases")
        stored_by_id[case_id] = record
        stored_names.add(case_name)
    desired_by_id = {record["case_id"]: record for record in desired_records}

    stored_ids = set(stored_by_id)
    desired_ids = set(desired_by_id)
    if restart_mode == "resume" and desired_ids != stored_ids:
        raise ConfigurationError(
            "resume requires exactly the stored sweep plan; use --extend to add cases"
        )
    if restart_mode == "extend" and not stored_ids < desired_ids:
        raise ConfigurationError(
            "extend requires the edited JSON grid to be a strict superset of the stored sweep"
        )

    for record in stored_records:
        record["case_id"] = record.get("case_id") or _case_id(record["parameters"])
        record.setdefault("attempts", 0)

    if restart_mode == "extend":
        old_count = len(stored_records)
        next_index = old_count + 1
        for desired in desired_records:
            if desired["case_id"] in stored_ids:
                continue
            desired["case_name"] = f"case_{next_index:05d}_{desired['case_id']}"
            stored_records.append(desired)
            next_index += 1
        manifest.setdefault("extensions", []).append(
            {
                "extended_at": _utc_now(),
                "previous_case_count": old_count,
                "new_case_count": len(stored_records),
                "config_file": str(config_path),
            }
        )

    manifest["config_file"] = str(config_path)
    manifest["generation"] = config["generation"]
    manifest["plan_fingerprint"] = _plan_fingerprint(stored_records)
    return manifest


def _case_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_complete_case(
    record: dict[str, Any], case_directory: Path, spheres_ini_output_mode: int
) -> tuple[bool, dict[str, Any] | None]:
    result = _case_json(case_directory / "case.json")
    if result is None or result.get("status") != "complete" or result.get("return_code") != 0:
        return False, result
    if (
        result.get("case_name") != record["case_name"]
        or _case_id(result.get("parameters", {})) != record["case_id"]
    ):
        return False, result
    command = result.get("command")
    try:
        stored_output_mode = command[command.index("-O") + 1]
    except (AttributeError, IndexError, ValueError):
        return False, result
    if stored_output_mode != str(spheres_ini_output_mode):
        return False, result
    required_files = (
        "impact.0000",
        "material.cfg",
        "projectile.structure",
        "target.structure",
        "spheres_ini.stdout.log",
        "spheres_ini.stderr.log",
        MILUPHCUDA_SCRIPT_NAME,
    )
    if not all((case_directory / name).is_file() for name in required_files):
        return False, result
    if (case_directory / "impact.0000").stat().st_size == 0:
        return False, result
    with (case_directory / "impact.0000").open() as particle_file:
        first_row = next((line for line in particle_file if line.strip()), None)
    if first_row is None:
        return False, result
    columns = first_row.split()
    if spheres_ini_output_mode == SPHERES_INI_NO_DENSITY_OUTPUT_MODE:
        expected_columns = 9
    elif spheres_ini_output_mode == SPHERES_INI_OUTPUT_MODE:
        expected_columns = 10
    elif spheres_ini_output_mode == SPHERES_INI_SOLID_OUTPUT_MODE:
        expected_columns = 19
    else:
        try:
            expected_columns = 21 + int(columns[10])
        except (IndexError, ValueError):
            return False, result
    if len(columns) != expected_columns:
        return False, result
    return True, result


def _reconcile_cases(output_directory: Path, manifest: dict[str, Any]) -> None:
    spheres_ini_output_mode = manifest["mode"]["spheres_ini_output_mode"]
    for index, record in enumerate(manifest["cases"]):
        case_directory = output_directory / record["case_name"]
        complete, case_result = _is_complete_case(
            record, case_directory, spheres_ini_output_mode
        )
        if complete:
            manifest["cases"][index] = {
                **record,
                **case_result,
                "case_id": record["case_id"],
                "directory": str(case_directory),
                "attempts": max(record.get("attempts", 0), case_result.get("attempts", 0)),
            }
            continue
        if case_result and case_result.get("status") == "failed":
            manifest["cases"][index] = {
                **record,
                **case_result,
                "case_id": record["case_id"],
                "status": "failed",
                "attempts": max(record.get("attempts", 0), case_result.get("attempts", 0)),
            }
        elif record.get("status") == "failed":
            record.setdefault("attempts", 1)
        elif case_directory.exists() or record.get("status") in {
            "running",
            "complete",
            "interrupted",
        }:
            record["status"] = "interrupted"
        else:
            record["status"] = "pending"


def _archive_incomplete_case(
    output_directory: Path, case_directory: Path, record: dict[str, Any]
) -> None:
    if not case_directory.exists():
        return
    archive_root = output_directory / INCOMPLETE_ATTEMPTS_DIRECTORY
    archive_root.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = archive_root / (
        f"{record['case_name']}_attempt_{record.get('attempts', 0):03d}_{timestamp}"
    )
    case_directory.rename(destination)


def _run_case_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate()
    except (KeyboardInterrupt, SweepTermination) as cause:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise CaseInterrupted(cause, stdout, stderr) from cause
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@contextmanager
def _sweep_lock(output_directory: Path) -> Iterable[None]:
    lock_path = output_directory / ".sweep.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConfigurationError(
                f"another generator is already using this sweep: {output_directory}"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _execute_sweep(
    config_path: Path,
    config: dict[str, Any],
    records: list[dict[str, Any]],
    silent: bool,
    restart_mode: str | None,
    retry_failed: bool,
    spheres_ini_output_mode: int,
) -> int:
    paths = config["resolved_paths"]
    output_directory = paths["output_directory"]
    if restart_mode:
        manifest = _prepare_restart_manifest(
            output_directory,
            config_path,
            config,
            records,
            restart_mode,
            spheres_ini_output_mode,
        )
        _reconcile_cases(output_directory, manifest)
    else:
        manifest = _new_manifest(
            config_path, config, records, spheres_ini_output_mode
        )
    _persist_sweep_state(output_directory, manifest)

    for index, record in enumerate(manifest["cases"], start=1):
        if record["status"] == "complete":
            continue
        if record["status"] == "failed" and not retry_failed:
            continue
        case = record["parameters"]
        case_directory = output_directory / record["case_name"]
        _archive_incomplete_case(output_directory, case_directory, record)
        case_directory.mkdir()
        (case_directory / "spheres_ini.input").write_text(
            render_spheres_input(case, spheres_ini_output_mode)
        )
        shutil.copy2(paths["material_file"], case_directory / "material.cfg")
        command = [
            str(paths["spheres_ini_executable"]),
            "-H",
            "-G", "2",
            "-O", str(spheres_ini_output_mode),
            "-S", str(paths["spheres_ini_source"]),
            "-f", "spheres_ini.input",
            "-m", "material.cfg",
            "-o", "impact.0000",
        ]
        record = {
            **record,
            "directory": str(case_directory),
            "command": command,
            "status": "running",
            "attempts": record.get("attempts", 0) + 1,
            "started_at": _utc_now(),
            "return_code": None,
        }
        manifest["cases"][index - 1] = record
        _write_json_atomic(case_directory / "case.json", record)
        _persist_sweep_state(output_directory, manifest)
        if not silent:
            print(f"[{index}/{len(manifest['cases'])}] {record['case_name']}", flush=True)
        try:
            completed = _run_case_command(command, case_directory)
        except CaseInterrupted as interruption:
            (case_directory / "spheres_ini.stdout.log").write_text(interruption.stdout)
            (case_directory / "spheres_ini.stderr.log").write_text(interruption.stderr)
            record["status"] = "interrupted"
            record["ended_at"] = _utc_now()
            _write_json_atomic(case_directory / "case.json", record)
            _persist_sweep_state(output_directory, manifest)
            raise interruption.cause
        (case_directory / "spheres_ini.stdout.log").write_text(completed.stdout)
        (case_directory / "spheres_ini.stderr.log").write_text(completed.stderr)
        result = {**record, "return_code": completed.returncode, "ended_at": _utc_now()}
        if completed.returncode != 0:
            result["status"] = "failed"
            manifest["cases"][index - 1] = result
            _write_json_atomic(case_directory / "case.json", result)
            _persist_sweep_state(output_directory, manifest)
            raise RuntimeError(
                f"spheres_ini failed for {record['case_name']}; see its stderr log"
            )
        result["status"] = "complete"
        result["derived"] = _derived_metadata(case, case_directory, completed.stdout)
        result["miluphcuda"] = _planned_miluphcuda(
            config["execution"]["miluphcuda"],
            result["derived"]["simulation_end_time_s"],
        )
        _write_miluphcuda_script(case_directory, result["miluphcuda"])
        if not silent:
            print(
                f"miluphcuda execution disabled; would invoke in {case_directory}:",
                flush=True,
            )
            print(result["miluphcuda"]["command_text"], flush=True)
        _write_json_atomic(case_directory / "case.json", result)
        manifest["cases"][index - 1] = result
        _persist_sweep_state(output_directory, manifest)

    failed_count = sum(record["status"] == "failed" for record in manifest["cases"])
    if failed_count:
        raise RuntimeError(
            f"sweep has {failed_count} failed case(s); rerun with --resume --retry-failed"
        )
    return 0


def execute(
    config_path: Path,
    dry_run: bool = False,
    silent: bool = False,
    restart_mode: str | None = None,
    retry_failed: bool = False,
) -> int:
    config, cases = load_configuration(config_path)
    paths = config["resolved_paths"]
    _validate_runtime_paths(paths)
    records = _case_records(cases)
    spheres_ini_output_mode = _spheres_ini_output_mode(config["generation"])

    if dry_run:
        if restart_mode or retry_failed:
            raise ConfigurationError("--dry-run cannot be combined with restart options")
        if not silent:
            print(f"Validated {len(records)} hydrostatic SPH case(s).")
            for record in records:
                print(record["case_name"], json.dumps(record["parameters"], sort_keys=True))
        return 0
    if retry_failed and restart_mode is None:
        raise ConfigurationError("--retry-failed requires --resume or --extend")

    output_directory = paths["output_directory"]
    if restart_mode:
        if not output_directory.is_dir():
            raise ConfigurationError(
                f"{restart_mode} requires an existing sweep directory: {output_directory}"
            )
    else:
        if output_directory.exists():
            raise ConfigurationError(f"output directory already exists: {output_directory}")
        output_directory.mkdir(parents=True)
    with _sweep_lock(output_directory):
        return _execute_sweep(
            config_path,
            config,
            records,
            silent,
            restart_mode,
            retry_failed,
            spheres_ini_output_mode,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="path to the sweep JSON file")
    parser.add_argument("--dry-run", action="store_true", help="validate and list cases without writing output")
    parser.add_argument(
        "--silent",
        action="store_true",
        help="suppress terminal output; diagnostics remain in per-case log files",
    )
    restart_group = parser.add_mutually_exclusive_group()
    restart_group.add_argument(
        "--resume",
        action="store_true",
        help="continue an existing sweep with exactly the stored case plan",
    )
    restart_group.add_argument(
        "--extend",
        action="store_true",
        help="add cases from a strict-superset grid and run unfinished cases",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="retry failed cases while resuming or extending a sweep",
    )
    arguments = parser.parse_args(argv)
    restart_mode = "resume" if arguments.resume else "extend" if arguments.extend else None

    def terminate(signum: int, _frame: Any) -> None:
        raise SweepTermination(signum)

    previous_sigterm_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        return execute(
            arguments.config.resolve(),
            arguments.dry_run,
            arguments.silent,
            restart_mode,
            arguments.retry_failed,
        )
    except (ConfigurationError, OSError, RuntimeError) as error:
        if not arguments.silent:
            print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if not arguments.silent:
            print("interrupted", file=sys.stderr)
        return 130
    except SweepTermination as termination:
        if not arguments.silent:
            print(f"terminated by signal {termination.signum}", file=sys.stderr)
        return 128 + termination.signum
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)


if __name__ == "__main__":
    raise SystemExit(main())
