"""Calibración de la cota de redondeo de la normalización espacial (T-027, TK-004).

Compara, sobre los modelos de extremos no rígidos de ``test_fem3d_end_penalty.grid_frame``, la
cota ``c·eps·Σ|k_ij·u_j|`` que usa hoy ``_Normalizer._bounds`` (solo el u de los nudos de
dominio: en los extremos con aux se usa el del maestro y se omite el término del resorte) con la
exacta (u de los aux de ``raw.end_aux``). Informa del cociente nuevo/antiguo y de su magnitud
frente a ``tol_equilibrium·P`` (la escala de la carga de los modelos). Es solo de lectura.

Uso: ``python tests/calibration/calibrate_spatial_bounds.py`` (desde la raíz del repositorio).
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS))

from test_fem3d_end_penalty import P, grid_frame, translate  # noqa: E402

from veritas.fem.results.run import RunStatus  # noqa: E402
from veritas.fem.spatial import analyze_spatial, normalize_spatial  # noqa: E402
from veritas.fem.spatial.config import SpatialAnalysisConfiguration  # noqa: E402
from veritas.fem.spatial.postconditions import rounding_bounds  # noqa: E402

KINDS = ("uy", "uz_free", "uyuz", "rz", "ryrz")
SLENDER = (5.0, 50.0, 500.0)


def measure(kind: str, slender: float, both: bool) -> str:
    frame = grid_frame(slender, kind, both=both)
    config = SpatialAnalysisConfiguration(frame.case_id)
    run = analyze_spatial(frame.model, config)
    if run.status is not RunStatus.SUCCEEDED or run.raw is None:
        return f"{kind:8} {slender:6g} {both!s:5}  corrida {run.status}"
    request = translate(frame)
    tag_of = {n.entity_id: n.tag for n in request.nodes}
    old_u = {tag_of[str(n.node_id)]: n.displacement for n in run.raw.nodes}
    new_u = dict(old_u)
    aux_tag = {(a.beam, a.end): a.tag for a in request.aux_nodes}
    beam_tag = {b.entity_id: b.tag for b in request.beams}
    for item in run.raw.end_aux:
        new_u[aux_tag[(beam_tag[str(item.member_id)], item.end)]] = item.displacement
    old = rounding_bounds(request, old_u)
    new = rounding_bounds(request, new_u)
    ratio = max(
        (new[n][i] / old[n][i] if old[n][i] else float("inf"))
        for n in new
        for i in (0, 1)
        if new[n][i]
    )
    big = max(max(v) for v in new.values())
    tol = config.tol_equilibrium
    normalized = normalize_spatial(run, frame.model)
    return (
        f"{kind:8} {slender:6g} {both!s:5}  máx(nuevo/antiguo) = {ratio:8.3g}  "
        f"máx cota nueva = {big:9.3e}  /(tol·P) = {big / (tol * P):9.3e}  "
        f"normalización: {type(normalized).__name__}"
    )


def main() -> None:
    print("tipo     esbelt. both   resultados")
    for kind in KINDS:
        for slender in SLENDER:
            for both in (False, True):
                print(measure(kind, slender, both))


if __name__ == "__main__":
    main()
