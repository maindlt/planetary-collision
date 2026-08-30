#!/usr/bin/env python3
"""Generate a Cartesian sweep of hydrostatic spheres_ini collision setups."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


G_SI = 6.6741e-11
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


class ConfigurationError(ValueError):
    """Raised when a sweep configuration is invalid."""


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


def render_spheres_input(case: dict[str, float]) -> str:
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
        "weibull_mantle": 0,
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
    lines = [
        "# Generated by scripts/generate_initial_conditions.py.",
        "# Hydrostatic iron-core/basalt-mantle bodies; no shell, damage, or spin.",
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
    if set(config) != {"paths", "execution", "parameters"}:
        raise ConfigurationError("the JSON root must contain exactly paths, execution, and parameters")

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
    if not isinstance(execution, dict) or set(execution) != {"max_cases"}:
        raise ConfigurationError("execution must contain exactly max_cases")
    max_cases = execution["max_cases"]
    if isinstance(max_cases, bool) or not isinstance(max_cases, int) or max_cases < 1:
        raise ConfigurationError("execution.max_cases must be a positive integer")

    cases = expand_cases(config["parameters"])
    if len(cases) > max_cases:
        raise ConfigurationError(
            f"sweep expands to {len(cases)} cases, exceeding max_cases={max_cases}"
        )
    return config, cases


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
    collision_timescale = combined_radius / impact_velocity
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
        "simulation_end_time_s": (case["f_i"] + case["f_t"]) * collision_timescale,
    }


def _case_records(cases: Iterable[dict[str, float]]) -> list[dict[str, Any]]:
    records = []
    for index, case in enumerate(cases, start=1):
        case_name = f"case_{index:05d}_{_case_id(case)}"
        records.append({"case_name": case_name, "parameters": case})
    return records


def execute(config_path: Path, dry_run: bool = False) -> int:
    config, cases = load_configuration(config_path)
    paths = config["resolved_paths"]
    _validate_runtime_paths(paths)
    records = _case_records(cases)

    if dry_run:
        print(f"Validated {len(records)} hydrostatic SPH case(s).")
        for record in records:
            print(record["case_name"], json.dumps(record["parameters"], sort_keys=True))
        return 0

    output_directory = paths["output_directory"]
    if output_directory.exists():
        raise ConfigurationError(f"output directory already exists: {output_directory}")
    output_directory.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "config_file": str(config_path),
        "generator": str(Path(__file__).resolve()),
        "mode": {
            "hydrostatic_structure": True,
            "particle_geometry": "SEAGen spherical shells",
            "output": "miluphcuda hydro",
            "solid_mechanics": False,
            "fragmentation_damage": False,
        },
        "case_count": len(records),
        "cases": [],
    }
    manifest_path = output_directory / "manifest.json"
    _write_json_atomic(manifest_path, manifest)

    for index, record in enumerate(records, start=1):
        case = record["parameters"]
        case_directory = output_directory / record["case_name"]
        case_directory.mkdir()
        (case_directory / "spheres_ini.input").write_text(render_spheres_input(case))
        shutil.copy2(paths["material_file"], case_directory / "material.cfg")
        command = [
            str(paths["spheres_ini_executable"]),
            "-H",
            "-G", "2",
            "-O", "0",
            "-S", str(paths["spheres_ini_source"]),
            "-f", "spheres_ini.input",
            "-m", "material.cfg",
            "-o", "impact.0000",
        ]
        print(f"[{index}/{len(records)}] {record['case_name']}", flush=True)
        completed = subprocess.run(
            command,
            cwd=case_directory,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        (case_directory / "spheres_ini.stdout.log").write_text(completed.stdout)
        (case_directory / "spheres_ini.stderr.log").write_text(completed.stderr)
        result = {
            **record,
            "directory": str(case_directory),
            "command": command,
            "return_code": completed.returncode,
        }
        if completed.returncode != 0:
            result["status"] = "failed"
            manifest["cases"].append(result)
            _write_json_atomic(manifest_path, manifest)
            raise RuntimeError(
                f"spheres_ini failed for {record['case_name']}; see its stderr log"
            )
        result["status"] = "complete"
        result["derived"] = _derived_metadata(case, case_directory, completed.stdout)
        _write_json_atomic(case_directory / "case.json", result)
        manifest["cases"].append(result)
        _write_json_atomic(manifest_path, manifest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="path to the sweep JSON file")
    parser.add_argument("--dry-run", action="store_true", help="validate and list cases without writing output")
    arguments = parser.parse_args(argv)
    try:
        return execute(arguments.config.resolve(), arguments.dry_run)
    except (ConfigurationError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
