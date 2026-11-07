"""Captura geométrica sin Qt (plan T-030 E3a; R3-D2…D5).

Funciones puras para modelar con el ratón: plano de trabajo, rayo→plano, rejilla, snapping,
cotas del gesto y entrada numérica. No importa Qt, PyVista ni el dominio de edición: el visor
aporta la proyección (``project``: punto → píxel) y dos puntos del rayo del cursor; aquí solo se
decide qué punto del modelo se captura. Unidades SI (m), base global X, Y, Z (ADR-001 §1).

Límites declarados:

- Sin buffer de profundidad (como ``picking``): entre candidatos del mismo tipo gana el más cercano
  al cursor en pantalla, no a la cámara.
- Capturar un punto de una barra (``ON_MEMBER``/``MIDPOINT``) solo da la coordenada; dividir la
  barra es E6.
- Prioridad fija: NODE > MIDPOINT > ON_MEMBER > GRID > FREE; con empate de distancia gana el
  índice menor.
- Importa ``forms`` (análisis de números y unidades), que a su vez importa el dominio de edición
  (``ModelEditor``); sigue sin Qt ni PyVista.
- Contrato del rayo: ``near`` y ``far`` son los puntos de mundo que corresponden a los puntos de
  display z = 0 (cerca) y z = 1 (lejos) bajo el cursor.
"""

import math
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError
from veritas.core.presentation import PROFILE_SI, PresentationProfile
from veritas.gui import forms
from veritas.gui.picking import MEMBER_RADIUS, NODE_RADIUS, Pixel, distance_to_segment

type Point = tuple[float, float, float]

DEFAULT_GRID_STEP: Final = 0.5
"""Paso de rejilla por defecto (m)."""

MIN_GRID_STEP: Final = 1e-6
"""Paso de rejilla mínimo (m): por debajo, ``round`` desborda o el redondeo no tiene sentido."""
COINCIDENCE_TOLERANCE: Final = 1e-9
"""Distancia (m) bajo la cual dos puntos son el mismo nudo (la del dominio)."""

_PARALLEL: Final = 1e-12
_ABSOLUTE_COMPONENTS: Final = 3
_PLAIN_NUMBER: Final = re.compile(r"[+-]?(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][+-]?\d+)?")


def _error(key: str, **params: str | float) -> InputValueError:
    text = {k: (f"{v:g}" if isinstance(v, float) else v) for k, v in params.items()}
    return InputValueError(Diagnostic.create(DiagnosticCode.INPUT_OUT_OF_RANGE, key, text))


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2]))


# --- Plano de trabajo y rejilla ------------------------------------------------------------


class PlaneAxis(StrEnum):
    """Eje normal al plano de trabajo."""

    X = "X"
    Y = "Y"
    Z = "Z"

    @property
    def position(self) -> int:
        return ("X", "Y", "Z").index(self.value)


@dataclass(frozen=True, slots=True)
class WorkPlane:
    """Plano ``coordenada(axis) = offset`` (por defecto horizontal Z = 0)."""

    axis: PlaneAxis = PlaneAxis.Z
    offset: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.axis, PlaneAxis):
            raise TypeError("WorkPlane.axis debe ser PlaneAxis")
        if not math.isfinite(self.offset):
            raise ValueError("WorkPlane.offset debe ser finito")

    def project(self, point: Point) -> Point:
        """Proyección ortogonal de ``point`` sobre el plano."""
        coords = list(point)
        coords[self.axis.position] = self.offset
        return (coords[0], coords[1], coords[2])


def ray_plane(
    near: Point, far: Point, plane: WorkPlane, *, max_distance: float | None = None
) -> Point | None:
    """Corte de la recta ``near``→``far`` con el plano; ``None`` si es paralela o va hacia atrás.

    ``near`` es el punto del rayo más cercano a la cámara (display z = 0); un corte con parámetro
    negativo (detrás de la cámara) se descarta. El paralelismo se mide relativo a la longitud del
    rayo (no depende de la escala de la escena). Con ``max_distance`` (m desde ``near``) se
    descarta un corte más lejano, p. ej. cuando el rayo casi rasante cae fuera de la escena.
    """
    length = math.dist(near, far)
    if length == 0.0:
        return None
    k = plane.axis.position
    delta = far[k] - near[k]
    if abs(delta) <= _PARALLEL * length:
        return None
    t = (plane.offset - near[k]) / delta
    if t < 0.0 or (max_distance is not None and t * length > max_distance):
        return None
    return _lerp(near, far, t)


def grid_point(point: Point, plane: WorkPlane, step: float) -> Point:
    """Nudo de rejilla más cercano en el plano (origen en 0; la coordenada normal es el offset)."""
    if not (math.isfinite(step) and step >= MIN_GRID_STEP):
        raise _error("capture.bad_step", step=step)
    coords = list(plane.project(point))
    for i in range(3):
        if i != plane.axis.position:
            ratio = coords[i] / step
            if not math.isfinite(ratio):
                raise _error("capture.bad_step", step=step)
            coords[i] = round(ratio) * step + 0.0  # + 0.0 evita -0.0
    return (coords[0], coords[1], coords[2])


def levels(nodes: Sequence[Point], axis: PlaneAxis) -> tuple[float, ...]:
    """Cotas distintas (ordenadas) de los nudos según ``axis``: ayuda para elegir el plano."""
    found: list[float] = []
    for value in sorted(node[axis.position] for node in nodes):
        if not found or value - found[-1] > COINCIDENCE_TOLERANCE:
            found.append(value)
    return tuple(found)


def coincident_node(
    point: Point, nodes: Sequence[Point], tol: float = COINCIDENCE_TOLERANCE
) -> int | None:
    """Índice del nudo a ≤ ``tol`` de ``point`` (el más cercano), o ``None``.

    Sirve para reutilizar un nudo existente en vez de intentar duplicarlo.
    """
    best: tuple[float, int] | None = None
    for index, node in enumerate(nodes):
        distance = math.dist(point, node)
        if distance <= tol and (best is None or distance < best[0]):
            best = (distance, index)
    return None if best is None else best[1]


# --- Snapping ------------------------------------------------------------------------------


class SnapKind(StrEnum):
    """Qué se capturó; el orden de declaración es la prioridad."""

    NODE = "NODE"
    MIDPOINT = "MIDPOINT"
    ON_MEMBER = "ON_MEMBER"
    GRID = "GRID"
    FREE = "FREE"


ALL_SNAPS: Final = frozenset(SnapKind)


@dataclass(frozen=True, slots=True)
class SnapResult:
    """Punto capturado, de qué tipo y (si procede) el nudo o la barra que lo origina."""

    point: Point
    kind: SnapKind
    node: int | None = None
    member: int | None = None


def _closest_on_segment_to_ray(near: Point, far: Point, a: Point, b: Point) -> Point:
    """Punto del segmento ``a``-``b`` más cercano a la recta ``near``-``far`` (en 3D)."""
    d1 = _sub(far, near)
    d2 = _sub(b, a)
    r = _sub(near, a)
    a11, a22, a12 = _dot(d1, d1), _dot(d2, d2), _dot(d1, d2)
    b1, b2 = _dot(d1, r), _dot(d2, r)
    if a22 == 0.0:
        return a
    denominator = a11 * a22 - a12 * a12
    if abs(denominator) <= _PARALLEL * max(a11 * a22, 1.0):
        t = 0.0 if a11 == 0.0 else b2 / a22  # recta paralela: proyecta ``near`` sobre el segmento
    else:
        t = (a11 * b2 - a12 * b1) / denominator
    return _lerp(a, b, max(0.0, min(1.0, t)))


def _nearest(pixel: Pixel, candidates: Sequence[tuple[int, Pixel]], radius: float) -> int | None:
    """Índice original del candidato más cercano a ``pixel`` dentro de ``radius``."""
    best: tuple[float, int] | None = None
    for index, other in candidates:
        distance = math.dist(pixel, other)
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, index)
    return None if best is None else best[1]


def _on_member(  # noqa: PLR0913, PLR0917 (datos del visor, uso interno)
    pixel: Pixel,
    ray: tuple[Point, Point],
    nodes: Sequence[Point],
    projected: Sequence[Pixel],
    members: Sequence[tuple[int, tuple[int, int]]],
    radius: float,
    project: Callable[[Point], Pixel],
) -> SnapResult | None:
    best: tuple[float, int, Point] | None = None
    for index, (i, j) in members:
        if distance_to_segment(pixel, projected[i], projected[j]) > radius:
            continue
        point = _closest_on_segment_to_ray(ray[0], ray[1], nodes[i], nodes[j])
        distance = math.dist(pixel, project(point))
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, index, point)
    if best is None:
        return None
    return SnapResult(best[2], SnapKind.ON_MEMBER, member=best[1])


def snap(  # noqa: PLR0913, PLR0911 (entradas del visor por nombre; una salida por prioridad)
    pixel: Pixel,
    ray: tuple[Point, Point],
    *,
    project: Callable[[Point], Pixel],
    nodes: Sequence[Point],
    members: Sequence[tuple[int, int]],
    plane: WorkPlane,
    step: float = DEFAULT_GRID_STEP,
    modes: Collection[SnapKind] = ALL_SNAPS,
    node_radius: float = NODE_RADIUS,
    member_radius: float = MEMBER_RADIUS,
    projected: Sequence[Pixel] | None = None,
    exclude_nodes: Collection[int] = (),
    exclude_members: Collection[int] = (),
    max_distance: float | None = None,
) -> SnapResult | None:
    """Punto a capturar bajo ``pixel``; ``None`` si no hay nada que capturar.

    ``ray`` son dos puntos de mundo del rayo del cursor (cerca y lejos). ``project`` lleva un punto
    del modelo al píxel donde se ve; ``projected`` (opcional) son ya los píxeles de ``nodes`` (el
    visor los tiene al pintar y evita reproyectarlos en cada movimiento del ratón).
    ``exclude_nodes``/``exclude_members`` (índices) no se capturan: lo que se está moviendo y las
    barras que llegan a ello. Una barra con un extremo excluido tampoco se captura. Sin candidato
    de nudo o barra, el rayo se corta con el plano de trabajo (rejilla o libre); si es paralelo al
    plano, apunta hacia atrás o supera ``max_distance``, ``None``.
    """
    pixels = [project(p) for p in nodes] if projected is None else list(projected)
    if len(pixels) != len(nodes):
        raise ValueError("projected debe tener un píxel por nudo")
    skip_nodes = frozenset(exclude_nodes)
    skip_members = frozenset(exclude_members)
    live = [
        (index, (i, j))
        for index, (i, j) in enumerate(members)
        if index not in skip_members and i not in skip_nodes and j not in skip_nodes
    ]
    if SnapKind.NODE in modes:
        best = _nearest(
            pixel, [(i, p) for i, p in enumerate(pixels) if i not in skip_nodes], node_radius
        )
        if best is not None:
            return SnapResult(nodes[best], SnapKind.NODE, node=best)
    if SnapKind.MIDPOINT in modes:
        mids = {index: _lerp(nodes[i], nodes[j], 0.5) for index, (i, j) in live}
        best = _nearest(pixel, [(k, project(m)) for k, m in mids.items()], member_radius)
        if best is not None:
            return SnapResult(mids[best], SnapKind.MIDPOINT, member=best)
    if SnapKind.ON_MEMBER in modes:
        found = _on_member(pixel, ray, nodes, pixels, live, member_radius, project)
        if found is not None:
            return found
    hit = ray_plane(ray[0], ray[1], plane, max_distance=max_distance)
    if hit is None:
        return None
    if SnapKind.GRID in modes:
        return SnapResult(grid_point(hit, plane, step), SnapKind.GRID)
    if SnapKind.FREE in modes:
        return SnapResult(hit, SnapKind.FREE)
    return None


# --- Cotas y entrada numérica --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dimensions:
    """Cotas provisionales de un tramo: longitud y diferencias por eje (m)."""

    length: float
    dx: float
    dy: float
    dz: float

    def text(self) -> str:
        return (
            f"L = {self.length:.3f} m  ΔX = {self.dx:.3f}  ΔY = {self.dy:.3f}  ΔZ = {self.dz:.3f}"
        )


def dimensions(start: Point, end: Point) -> Dimensions:
    """Cotas de ``start`` a ``end``."""
    dx, dy, dz = _sub(end, start)
    return Dimensions(math.hypot(dx, dy, dz), dx, dy, dz)


def cursor_text(point: Point) -> str:
    """Coordenadas del cursor para la barra de estado."""
    return f"X = {point[0]:.3f}  Y = {point[1]:.3f}  Z = {point[2]:.3f} m"


def parse_entry(
    text: str,
    *,
    anchor: Point | None,
    cursor: Point | None,
    profile: PresentationProfile = PROFILE_SI,
) -> Point:
    """Punto tecleado durante un gesto.

    - «L»: longitud (> 0) desde ``anchor`` en la dirección del ``cursor`` (necesita ambos y que no
      coincidan).
    - «x; y; z»: coordenadas absolutas (separadas por «;»: la unidad puede llevar espacio).
    - «@x; y; z»: incrementos desde ``anchor``.

    La coma decimal se admite y ``1,000`` se rechaza por ambiguo (como en ``forms``); cada valor
    admite unidad de longitud y, sin ella, la del ``profile``. Diagnóstico en español en cualquier
    rechazo.
    """
    if not isinstance(text, str) or not text.strip():
        raise _error("forms.empty_value", field="Entrada")
    body = text.strip()
    relative = body.startswith("@")
    if relative:
        body = body[1:].strip()
    tokens = body.split()
    if ";" not in body and len(tokens) > 1 and all(_PLAIN_NUMBER.fullmatch(t) for t in tokens):
        raise _error("capture.entry_separator", text=text)
    parts = [p.strip() for p in body.split(";")]
    values = [forms.parse_length(p, field="Entrada", profile=profile).si_value for p in parts]
    if len(values) == _ABSOLUTE_COMPONENTS:
        if not relative:
            return (values[0], values[1], values[2])
        if anchor is None:
            raise _error("capture.entry_no_anchor")
        return (anchor[0] + values[0], anchor[1] + values[1], anchor[2] + values[2])
    if len(values) == 1 and not relative:
        if anchor is None or cursor is None:
            raise _error("capture.entry_no_anchor")
        if values[0] <= 0.0:
            raise _error("capture.entry_bad_length", text=text)
        direction = dimensions(anchor, cursor)
        if direction.length < COINCIDENCE_TOLERANCE:
            raise _error("capture.entry_zero_direction")
        scale = values[0] / direction.length
        return (
            anchor[0] + direction.dx * scale,
            anchor[1] + direction.dy * scale,
            anchor[2] + direction.dz * scale,
        )
    raise _error("capture.entry_components", text=text)
