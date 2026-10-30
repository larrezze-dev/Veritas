"""Penalización iterada en los extremos de barra (ADR-004, plan T-018, pieza E2.4-impl).

Modelos del dominio traducidos con ``translate_spatial`` y ejecutados
con el worker 3D: rejilla del hecho 22 (dos barras oblicuas I-J-K empotradas en I y K, extremo
J de B1 no rígido) y casos aleatorios de giro (nudo J con 1-4 barras). Referencia: la K
reducida de la salvaguarda (``reference_displacements(aux=True)``) y ``localForce`` con la k y
el p0 de la salvaguarda. Los tests afirman «OK ⇒ error ≤ cota», no un número de iteraciones
(ADR-004, Consecuencias: el estancamiento depende del redondeo de la plataforma).
"""

import dataclasses
import json
import math
import random
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import test_fem3d_singularity as cases
from fem3d_support import reference_displacements
from fem_support import Frame
from test_fem3d_analyze import analyze, assert_failed, wrapper
from test_fem3d_singularity import NORMAL
from test_fem3d_singularity_ends import _local_force
from test_fem3d_solver import WORKER_3D, run
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, Severity
from veritas.core.loads.loads import LoadBasis
from veritas.core.messages_es import render_message
from veritas.core.supports.support import (
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.adapters.protocol import WorkerStatus
from veritas.fem.adapters.singularity import InvalidStiffnessError
from veritas.fem.results.run import RunStatus
from veritas.fem.runners.subprocess_runner import run_worker
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    PROTOCOL_VERSION,
    SpatialRequest,
    SpatialWorkerResponse,
    penalty_step,
)
from veritas.fem.spatial.stiffness import force_scale, spring_direction, validate_request
from veritas.fem.spatial.translator import (
    HYPOTHESIS_END_PENALTY,
    HYPOTHESIS_PENALTY,
    translate_spatial,
)

pytestmark = pytest.mark.solver

E = 210e9
WIDTH, HEIGHT = 0.2, 0.4
I_WEAK = HEIGHT * WIDTH**3 / 12
AREA = WIDTH * HEIGHT
R_WEAK = math.sqrt(I_WEAK / AREA)  # Radio de giro débil: L = λ·r.
D1 = (1.2, -0.7, 0.9)
D2 = (0.3, 1.0, -0.4)
P = 1e4
TOL_PENALTY = 1e-6
TIGHT = 1e-8  # Cota del hecho 22 con λ ≤ 500 (medido: ≤ 3,8e-11).
KINDS = ("uy", "uz_free", "uyuz", "rz", "ryrz")
FIXTURES = Path(__file__).parent / "data" / "fem3d_v2_responses.json"
WORKER_V2 = Path(__file__).parent / "data" / "opensees_worker_3d_v2.py"
FIXTURE_REL = 1e-9  # Respuestas guardadas: misma cota relativa que las pruebas del solver.


def _unit(v: tuple[float, ...] | list[float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(x * x for x in v))
    return v[0] / n, v[1] / n, v[2] / n


def _connection(kind: str, length: float) -> EndConnection:
    """Uy/Uz con resorte (o Uz liberado) y Ry/Rz con resorte de rigidez EI/L y 12EI/L³."""
    ei = E * I_WEAK
    k_rot = Quantity.of(ei / length, "N*m/rad")
    k_tr = Quantity.of(12 * ei / length**3, "N/m")
    c = EndConnection.rigid()
    if "uy" in kind:
        c = c.with_dof(Dof.UY, DofState.SPRING, k_tr)
    if kind == "uz_free":
        c = c.with_dof(Dof.UZ, DofState.RELEASED)
    elif "uz" in kind:
        c = c.with_dof(Dof.UZ, DofState.SPRING, k_tr)
    if "ry" in kind:
        c = c.with_dof(Dof.RY, DofState.SPRING, k_rot)
    if "rz" in kind:
        c = c.with_dof(Dof.RZ, DofState.SPRING, k_rot)
    return c


def grid_frame(slender: float, kind: str, *, both: bool = False, chain: bool = False) -> Frame:
    """Hecho 22: I-J-K oblicuas (alfa = 0,37 y −0,2), carga nodal en J y carga local en B1."""
    length = slender * R_WEAK
    f = Frame()
    d1, d2 = _unit(D1), _unit(D2)
    xj = tuple(length * x for x in d1)
    xk = tuple(xj[i] + length * d2[i] for i in range(3))
    i = f.node(0.0, 0.0, y=0.0)
    j = f.node(xj[0], xj[2], y=xj[1])
    k = f.node(xk[0], xk[2], y=xk[1])
    b1 = f.member(i, j, alpha=0.37)
    b2 = f.member(j, k, alpha=-0.2)
    f.support(i, fixed_restraints())
    f.support(k, fixed_restraints())
    if chain:
        f.support(
            j, build_restraints((SupportDof.UX,)), SupportBasis.inclined(NORMAL, (1.0, 0.0, 0.0))
        )
    f.nodal(
        j,
        fx=0.3 * P,
        fy=-0.2 * P,
        fz=-0.5 * P,
        mx=0.04 * P * length,
        my=-0.07 * P * length,
        mz=0.09 * P * length,
    )
    f.model.add_member_uniform_load(
        f.case_id,
        b1.id,
        basis=LoadBasis.LOCAL,
        qx=Quantity.of(0.0, "N/m"),
        qy=Quantity.of(-2e3, "N/m"),
        qz=Quantity.of(-5e3, "N/m"),
    )
    if kind == "rigid":  # Mismo montaje sin extremos no rígidos.
        return f
    f.model.set_member_end(b1.id, MemberEnd.J, _connection(kind, length))
    if both:
        f.model.set_member_end(b2.id, MemberEnd.I, _connection(kind, length))
    return f


def rotation_frame(rng: random.Random) -> Frame:
    """Nudo J con 1-4 barras empotradas en el otro extremo, direcciones y alfa aleatorios y
    resortes Ry/Rz en los extremos J de un subconjunto; cadena opcional (sonda de E2.4)."""
    count = rng.choice([1, 2, 3, 4])
    length = 10 ** rng.uniform(math.log10(5), math.log10(5000)) * R_WEAK
    ends = rng.randint(1, count)
    kinds = [rng.choice(["rz", "ry", "ryrz"]) for _ in range(ends)]
    ratios = [10 ** rng.uniform(-2, 2) for _ in range(ends)]
    chain = rng.random() < 0.3
    f = Frame()
    j = f.node(0.0, 0.0, y=0.0)
    members = []
    for _ in range(count):
        d = _unit([rng.gauss(0, 1) for _ in range(3)])
        ell = length * rng.uniform(0.5, 2.0)
        far = f.node(-ell * d[0], -ell * d[2], y=-ell * d[1])
        f.support(far, fixed_restraints())
        members.append((f.member(far, j, alpha=rng.uniform(-math.pi, math.pi)), ell))
    loads = [rng.uniform(-1, 1) for _ in range(6)]
    normal = _unit([rng.gauss(0, 1) for _ in range(3)])
    if chain:
        ref = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (1.0, 0.0, 0.0)
        f.support(j, build_restraints((SupportDof.UX,)), SupportBasis.inclined(normal, ref))
    ell0 = members[0][1]
    f.nodal(
        j,
        fx=P * loads[0],
        fy=P * loads[1],
        fz=P * loads[2],
        mx=P * ell0 * loads[3],
        my=P * ell0 * loads[4],
        mz=P * ell0 * loads[5],
    )
    f.model.add_member_uniform_load(
        f.case_id,
        members[0][0].id,
        basis=LoadBasis.LOCAL,
        qx=Quantity.of(0.0, "N/m"),
        qy=Quantity.of(-2e3, "N/m"),
        qz=Quantity.of(-5e3, "N/m"),
    )
    for (member, ell), kind, ratio in zip(members, kinds, ratios, strict=False):
        k = Quantity.of(ratio * E * I_WEAK / ell, "N*m/rad")
        c = EndConnection.rigid()
        if "ry" in kind:
            c = c.with_dof(Dof.RY, DofState.SPRING, k)
        if "rz" in kind:
            c = c.with_dof(Dof.RZ, DofState.SPRING, k)
        f.model.set_member_end(member.id, MemberEnd.J, c)
    return f


def translate(frame: Frame, **config: Any) -> SpatialRequest:
    return translate_spatial(
        frame.model, SpatialAnalysisConfiguration(frame.case_id, **config)
    ).request


def errors(request: SpatialRequest, response: SpatialWorkerResponse) -> tuple[float, float, float]:
    """Errores relativos (ADR-004 §4) frente a la K reducida: traslaciones y giros de dominio
    y aux, relativos a ‖u‖∞ y ‖θ‖∞ del modelo, y fuerzas de barra, relativas a
    máx(‖f_barra‖∞, F del caso)."""
    ref = reference_displacements(request, aux=True)
    disp = {tag: d for tag, d, _r in response.node_results}
    disp |= {tag: d for tag, d, _r in response.aux_results}
    trans = max(abs(v) for d in ref.values() for v in d[:3])
    rot = max(abs(v) for d in ref.values() for v in d[3:])
    error_u = max(abs(disp[t][c] - ref[t][c]) for t in ref for c in range(3)) / trans
    error_r = max(abs(disp[t][c] - ref[t][c]) for t in ref for c in range(3, 6)) / rot
    forces = dict(response.beam_forces)
    error_f = 0.0
    for beam in request.beams:
        expected = _local_force(request, ref, beam.tag)
        scale = max(*(abs(v) for v in expected), force_scale(request)[0])
        error_f = max(
            error_f,
            max(abs(a - b) for a, b in zip(forces[beam.tag], expected, strict=True)) / scale,
        )
    return error_u, error_r, error_f


def _check(request: SpatialRequest, bound: float) -> SpatialWorkerResponse:
    assert request.end_penalties, "el caso debe tener penalización de extremo"
    response = run(request)
    assert response.end_penalty_converged
    assert response.end_penalty_stop in ("tolerance", "stagnation")
    errs = errors(request, response)
    print(
        f"iteraciones {response.end_penalty_iterations} ({response.end_penalty_stop}), "
        f"errores u {errs[0]:.1e}, giro {errs[1]:.1e}, f {errs[2]:.1e}"
    )
    assert max(errs) <= bound, errs
    return response


# --- Traductor: factor de extremo, b_s, S_s e hipótesis ------------------------------------


def test_translator_sends_direction_scale_and_stop_parameters() -> None:
    frame = grid_frame(50, "uy")
    config = SpatialAnalysisConfiguration(frame.case_id)
    translation = translate_spatial(frame.model, config)
    request = translation.request
    springs = {s.tag: s for s in request.end_springs if s.penalty}
    assert {q.spring for q in request.end_penalties} == set(springs)
    force, longest = force_scale(request)
    for item in request.end_penalties:
        spring = springs[item.spring]
        assert item.direction == spring_direction(spring.axis_x, spring.axis_yp, spring.dof)
        assert item.scale == (force if spring.dof <= 3 else force * longest)
    assert (
        request.end_penalty_tolerance,
        request.end_penalty_stagnation,
        request.end_penalty_max_iterations,
    ) == (1e-10, config.tol_penalty, 20)
    assert HYPOTHESIS_END_PENALTY in translation.hypotheses
    assert HYPOTHESIS_PENALTY not in translation.hypotheses


def test_end_penalty_factor_scales_only_end_springs() -> None:
    frame = grid_frame(50, "ryrz")
    base = translate(frame)
    scaled = translate(frame, end_penalty_factor=2e1, penalty_factor=1e7)
    for a, b in zip(base.end_springs, scaled.end_springs, strict=True):
        assert b.stiffness == (2.0 * a.stiffness if a.penalty else a.stiffness)


def test_force_scale_definition() -> None:
    """F = máx(máx|P|, Σ|w|·L, máx|M|/L_máx) con las cargas traducidas."""
    request = translate(grid_frame(50, "uy"))
    length = 50 * R_WEAK
    nodal = request.nodal_loads[0].values
    expected = max(
        math.hypot(*nodal[:3]),
        math.hypot(2e3, 5e3) * length,
        math.hypot(*nodal[3:]) / length,
    )
    force, longest = force_scale(request)
    assert longest == pytest.approx(length, rel=1e-12)
    assert force == pytest.approx(expected, rel=1e-12)


# --- Hecho 22 con el criterio implementado --------------------------------------------------


@pytest.mark.parametrize("slender", [5, 500, 5000])
@pytest.mark.parametrize("kind", KINDS)
def test_grid_one_end(kind: str, slender: float) -> None:
    _check(translate(grid_frame(slender, kind)), TIGHT if slender <= 500 else TOL_PENALTY)


@pytest.mark.parametrize("layout", ["dos extremos", "cadena"])
@pytest.mark.parametrize("kind", KINDS)
def test_grid_two_ends_and_chain(kind: str, layout: str) -> None:
    frame = grid_frame(5000, kind, both=layout == "dos extremos", chain=layout == "cadena")
    _check(translate(frame), TOL_PENALTY)


def test_random_rotation_cases() -> None:
    """20 casos aleatorios de giro con semilla fija; registra la mediana de iteraciones."""
    rng = random.Random(20261030)
    iterations = []
    for _ in range(20):
        request = translate(rotation_frame(rng))
        if not request.end_penalties:  # Terna coordenada por azar: exacta, sin iterar.
            continue
        iterations.append(_check(request, TOL_PENALTY).end_penalty_iterations)
    assert len(iterations) >= 15
    print(f"iteraciones: mediana {statistics.median(iterations)}, máximo {max(iterations)}")


# --- Parada: λ ≈ 0, carga nula, estancamiento y no convergencia ----------------------------


def test_zero_load_converges_at_once() -> None:
    """Carga nula: S_s = 0 y k·g = 0 dan p_s = 0 (sin falsos FAILED)."""
    request = translate(grid_frame(50, "uyuz"))
    request = dataclasses.replace(
        request,
        nodal_loads=tuple(dataclasses.replace(p, values=(0.0,) * 6) for p in request.nodal_loads),
        beam_loads=(),
        end_penalties=tuple(dataclasses.replace(q, scale=0.0) for q in request.end_penalties),
    )
    response = run(request)
    assert (response.end_penalty_iterations, response.end_penalty_stop) == (1, "tolerance")
    assert all(abs(v) == 0.0 for _t, d, _r in response.node_results for v in d)


def test_link_without_force_converges_at_once() -> None:
    """λ ≈ 0 con S_s > 0: la carga actúa sobre un nudo empotrado y los enlaces no trabajan."""
    request = translate(grid_frame(50, "ryrz"))
    fixed = request.fixes[0].node
    request = dataclasses.replace(
        request,
        nodal_loads=tuple(dataclasses.replace(p, node=fixed) for p in request.nodal_loads),
        beam_loads=(),
    )
    assert all(q.scale > 0.0 for q in request.end_penalties)
    response = run(request)
    assert (response.end_penalty_iterations, response.end_penalty_stop) == (1, "tolerance")
    assert all(lam == 0.0 for *_rest, lam in response.end_spring_results)


def test_unreachable_tolerance_keeps_the_error_bound() -> None:
    """Tolerancia 1e-30: casi siempre para por estancamiento; si el paso llega a 0 exacto
    para por tolerancia. Los UUID de ``Frame`` cambian en cada corrida, y con ellos el orden de
    tags, la numeración RCM y el redondeo (medido: una corrida de la suite llegó a P = 0). En
    ambos casos converge con el error bajo ``tol_penalty``. El estancamiento determinista va
    en ``test_stop_rule_is_deterministic``."""
    request = translate(grid_frame(5000, "uyuz"), end_penalty_tolerance=1e-30)
    response = run(request)
    assert response.end_penalty_stop in ("stagnation", "tolerance")
    assert response.end_penalty_converged
    assert max(errors(request, response)) <= TOL_PENALTY


class _ScriptedOps:
    """OpenSees simulado para ``_iterate``: acepta toda llamada y devuelve, en cada
    resolución, la siguiente deformación de la lista para todos los resortes."""

    def __init__(self, deformations: list[float]) -> None:
        self.deformations = deformations
        self.solves = 0

    def analyze(self, _steps: int) -> int:
        self.solves += 1
        return 0

    def eleResponse(self, _tag: int, _kind: str) -> list[float]:  # noqa: N802 (API de OpenSees)
        return [self.deformations[self.solves - 1]]

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args: None


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        # Mejora < 10× en la iteración 4 con P ≤ tol_penalty: estancamiento.
        ([1e-3, 1e-5, 5e-7, 4e-7], (4, "stagnation")),
        # En la iteración 2 no se acepta estancamiento (i ≥ 3); en la 3 sí.
        ([1e-7, 9e-8, 8e-8], (3, "stagnation")),
        # Mejora 10× sostenida: sigue hasta la tolerancia.
        ([1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8, 1e-9, 1e-10], (8, "tolerance")),
        # Sin mejora pero P > tol_penalty: agota el máximo.
        ([1e-3] * 5, (5, "max_iterations")),
    ],
)
def test_stop_rule_is_deterministic(steps: list[float], expected: tuple[int, str]) -> None:
    """ADR-004 §3 con pasos guionizados: un resorte con k = 1 y S_s = 1, así p = |g|."""
    request = json.loads(translate(grid_frame(50, "uy")).to_json())
    (first, *_rest) = request["end_penalties"]
    request["end_penalties"] = [[first[0], first[1], 1.0]]
    request["end_springs"] = [
        [*s[:3], 1.0, *s[4:]] if s[0] == first[0] else s
        for s in request["end_springs"]
        if s[0] == first[0] or not s[6]
    ]
    request["end_penalty_max_iterations"] = 5 if expected[1] == "max_iterations" else 20
    ops = _ScriptedOps(steps)
    code, iterations, stop, multipliers = opensees_worker_3d._iterate(ops, request)
    assert (code, iterations, stop) == (0, *expected)
    # λ de la última resolución: suma de k·g de las anteriores.
    assert multipliers[first[0]] == pytest.approx(sum(steps[: iterations - 1]))


def test_validation_rejects_inconsistent_end_penalties() -> None:
    """La validación del padre exige una entrada por resorte de penalización, con b_s =
    ``spring_direction`` y S_s ≥ 0 (revisión de código de E2.4-impl)."""
    request = translate(grid_frame(50, "uy"))
    first = request.end_penalties[0]
    for penalties in (
        request.end_penalties[1:],
        (*request.end_penalties, first),
        (dataclasses.replace(first, direction=(1.0, 0.0, 0.0)), *request.end_penalties[1:]),
        (dataclasses.replace(first, scale=-1.0), *request.end_penalties[1:]),
    ):
        with pytest.raises(InvalidStiffnessError):
            validate_request(dataclasses.replace(request, end_penalties=penalties))
    validate_request(request)


def test_max_iterations_is_not_converged() -> None:
    request = translate(grid_frame(500, "uy"), end_penalty_max_iterations=1)
    response = run(request)
    assert (response.end_penalty_iterations, response.end_penalty_stop) == (1, "max_iterations")
    assert not response.end_penalty_converged
    forces = {tag: f for tag, f, _g, _lam in response.end_spring_results}
    step = max(penalty_step(forces[q.spring], q.scale) for q in request.end_penalties)
    assert step > request.end_penalty_tolerance


def test_link_force_and_multiplier_convention() -> None:
    """ADR-004 §6: λ de la última resolución; fuerza del enlace = λ + k·g, con k·g → 0."""
    request = translate(grid_frame(500, "uy"))
    response = run(request)
    penalized = {q.spring for q in request.end_penalties}
    springs = {s.tag: s for s in request.end_springs}
    for tag, force, deformation, lam in response.end_spring_results:
        assert force == pytest.approx(springs[tag].stiffness * deformation, rel=1e-12, abs=1e-300)
        if tag not in penalized:
            assert lam == 0.0
    link = max(abs(lam) for tag, *_r, lam in response.end_spring_results if tag in penalized)
    assert link > 1e3 * request.end_penalty_tolerance * force_scale(request)[0]


# --- Patrón corrector único y wipe en cada iteración ----------------------------------------


class _Recorder:
    """Envoltorio de ``openseespy.opensees`` que registra wipe, timeSeries, pattern y load."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        function = getattr(self.real, name)
        if name not in ("wipe", "timeSeries", "pattern", "analyze"):
            return function  # type: ignore[no-any-return]

        def record(*args: Any) -> Any:
            self.calls.append((name, args))
            return function(*args)

        return record


def _recorded(
    monkeypatch: pytest.MonkeyPatch, request: SpatialRequest
) -> list[tuple[str, tuple[Any, ...]]]:
    import openseespy.opensees as real  # type: ignore[import-untyped]  # noqa: PLC0415

    recorder = _Recorder(real)
    package = sys.modules["openseespy"]
    monkeypatch.setitem(sys.modules, "openseespy.opensees", recorder)
    monkeypatch.setattr(package, "opensees", recorder, raising=False)
    response = opensees_worker_3d._solve(json.loads(request.to_json()))
    assert response["status"] == WorkerStatus.OK, response["message"]
    return recorder.calls


def test_single_corrector_pattern_and_wipe_per_iteration(monkeypatch: pytest.MonkeyPatch) -> None:
    request = translate(grid_frame(500, "uyuz"))
    calls = _recorded(monkeypatch, request)
    analyses = [i for i, (name, _a) in enumerate(calls) if name == "analyze"]
    assert len(analyses) >= 2
    start = 0
    for end in analyses:
        block = calls[start:end]
        assert [name for name, _a in block].count("wipe") == 1
        tag = opensees_worker_3d.CORRECTOR_TAG
        assert [a for name, a in block if name == "pattern"] == [
            ("Plain", 1, 1),
            ("Plain", tag, tag),
        ]
        assert [a for name, a in block if name == "timeSeries"] == [
            ("Linear", 1),
            ("Constant", tag),
        ]
        start = end + 1


def test_no_end_penalty_path_has_no_corrector(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _recorded(monkeypatch, translate(grid_frame(500, "rigid")))
    assert [name for name, _a in calls].count("analyze") == 1
    assert [a for name, a in calls if name == "pattern"] == [("Plain", 1, 1)]


# --- Igualdad numérica sin penalización de extremo (fixtures v2) --------------------------


def _upgrade(request: dict[str, Any]) -> str:
    """Petición v2 capturada → versión actual sin penalización de extremo ni asientos."""
    assert not any(spring[6] for spring in request["end_springs"])
    return json.dumps(
        {
            **request,
            "version": PROTOCOL_VERSION,
            "end_penalties": [],
            "end_penalty_tolerance": 1e-10,
            "end_penalty_stagnation": TOL_PENALTY,
            "end_penalty_max_iterations": 20,
            "settlements": [],  # T-016: v4 sin asientos
        }
    )


def _flat(value: Any) -> list[float]:
    if isinstance(value, list):
        return [x for item in value for x in _flat(item)]
    return [float(value)]


def _groups(response: dict[str, Any]) -> dict[tuple[str, int], list[float]]:
    """Valores por bloque (clave, posición en la entrada): desplazamientos, reacciones y
    fuerzas se comparan cada uno con su propia escala."""
    groups: dict[tuple[str, int], list[float]] = {}
    for key in ("nodes", "grounds", "chains", "beams", "aux", "end_springs"):
        for entry in response[key]:
            for position, value in enumerate(entry[1:4], start=1):
                groups.setdefault((key, position), []).extend(_flat(value))
    return groups


def _numeric(response: dict[str, Any]) -> dict[str, Any]:
    result = {key: response[key] for key in ("nodes", "grounds", "chains", "beams", "aux")}
    result["end_springs"] = [item[:3] for item in response["end_springs"]]
    return result


def test_responses_without_end_penalty_match_v2() -> None:
    """ADR-004: el camino sin penalización de extremo no cambia.

    - Bit a bit: el worker v2 congelado (``data/opensees_worker_3d_v2.py``) con la petición
      v2 y el worker actual con la misma petición en v3, en la misma máquina.
    - Ancla: las respuestas guardadas antes del cambio (``FIXTURES``), con cota relativa
      ``FIXTURE_REL`` por bloque (otra máquina u otro sistema pueden diferir en el redondeo).
    """
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert data["cases"]
    for case in data["cases"]:
        old = run_worker(json.dumps(case["request"]), 60.0, worker=WORKER_V2)
        new = run_worker(_upgrade(case["request"]), 60.0, worker=WORKER_3D)
        assert old.response_text is not None, old.stderr
        assert new.response_text is not None, new.stderr
        before, after = json.loads(old.response_text), json.loads(new.response_text)
        assert before["status"] == after["status"] == "OK", after["message"]
        assert (
            after["end_penalty_iterations"],
            after["end_penalty_stop"],
            after["end_penalty_converged"],
        ) == (0, "none", True)
        assert all(item[3] == 0.0 for item in after["end_springs"])
        assert _numeric(after) == _numeric(before)
        stored = _groups(case["response"])
        for block, values in _groups(_numeric(after)).items():
            expected = stored[block]
            scale = max((abs(x) for x in expected), default=0.0)
            assert len(values) == len(expected), block
            assert all(
                abs(a - b) <= FIXTURE_REL * scale for a, b in zip(values, expected, strict=True)
            ), block


# --- Orquestación: FAILED sin convergencia y recomprobación del padre ----------------------


def test_not_converged_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``end_penalty_max_iterations`` = 1 con un enlace con fuerza: FAILED, nunca OK."""
    run_ = analyze(grid_frame(500, "uy"), end_penalty_max_iterations=1)
    assert_failed(run_, DiagnosticCode.ANALYSIS_NONCONVERGENCE, "fem3d.end_penalty_not_converged")
    assert run_.diagnostics[0].params_dict()["stop"] == "max_iterations"


@pytest.mark.parametrize(
    "after",
    [
        # El worker declara convergencia pero el paso recalculado no la cumple.
        "[s.__setitem__(1, s[1] * 1e6 + 1.0) for s in data['end_springs']]",
        # Estancamiento declarado antes de la iteración 3.
        "data.update(end_penalty_stop='stagnation', end_penalty_iterations=1)",
    ],
    ids=["paso", "estancamiento temprano"],
)
def test_parent_rechecks_convergence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, after: str
) -> None:
    run_ = analyze(grid_frame(500, "uy"), wrapper(tmp_path, after=after))
    assert_failed(run_, DiagnosticCode.ANALYSIS_NONCONVERGENCE, "fem3d.end_penalty_not_converged")


@pytest.mark.parametrize(
    "after",
    [
        "data['aux'] = []",  # Aux de la respuesta que no corresponde a la petición.
        "data['end_springs'] = data['end_springs'][1:]",  # Resortes de extremo que faltan.
    ],
    ids=["aux", "resortes de extremo"],
)
def test_response_ends_not_matching_request(tmp_path: Path, after: str) -> None:
    """Revisión de E2, M1: aux o resortes de extremo de la respuesta que no corresponden a
    la petición dan FAILED con ``fem.protocol_error``, nunca OK."""
    run_ = analyze(grid_frame(500, "uy"), wrapper(tmp_path, after=after))
    assert_failed(run_, DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error")


def test_iterations_without_end_penalty_is_protocol_error(tmp_path: Path) -> None:
    """Revisión de E2, M1: iteraciones declaradas en un modelo sin penalización de extremo."""
    after = "data.update(end_penalty_iterations=2, end_penalty_stop='tolerance')"
    run_ = analyze(cases._loaded_cantilever(cases.OBLIQUE), wrapper(tmp_path, after=after))
    assert_failed(run_, DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error")


def test_iterations_diagnostic_is_informative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Con penalización de extremo convergida, ``fem3d.end_penalty_iterations`` (WARNING
    informativo) se registra y la corrida termina OK con los controles de E2.5."""
    run_ = analyze(grid_frame(500, "uy"))
    assert run_.status is RunStatus.SUCCEEDED, run_.diagnostics
    found = [d for d in run_.diagnostics if d.message_key == "fem3d.end_penalty_iterations"]
    assert len(found) == 1
    assert found[0].severity is Severity.WARNING
    assert found[0].params_dict()["stop"] in ("tolerance", "stagnation")
    render_message(found[0])


def test_rigid_run_has_no_iterations_diagnostic() -> None:
    run_ = analyze(grid_frame(500, "rigid"))
    assert run_.status is RunStatus.SUCCEEDED
    assert all(d.message_key != "fem3d.end_penalty_iterations" for d in run_.diagnostics)
