"""Secciones de croquis libre: contornos poligonales con huecos (T-029 E1; ADR-013).

``FreeformParameters`` es la geometría de una revisión de la familia ``FREEFORM``: un contorno
exterior y cero o más huecos, cada uno una lista de vértices (y, z) en m, en el sistema de
definición de la sección (ADR-001 §1: y según b, z según h). No hay arcos (T-029, D3-A).

Representación única (misma geometría ⇒ mismo contenido y mismo hash): el exterior se guarda
antihorario, los huecos horarios, cada contorno empieza en su vértice menor (y, luego z) y los
huecos se ordenan por su primer vértice. El contorno se cierra implícitamente (no se repite el
primer vértice).

Validez (EDT-004), con tolerancia ``TOLERANCE_RELATIVE · diagonal`` de la caja del exterior:
cada contorno tiene ≥ 3 vértices finitos distintos, área no nula y es simple (ninguna arista
toca a otra no contigua; en aristas contiguas se rechaza el pico, es decir, el retroceso o el
solape); cada hueco queda estrictamente dentro del exterior y sin tocar ni contener a otro hueco.
Ante la duda se rechaza (D7-A).

Propiedades: fórmulas exactas de polígono (teorema de Green) referidas a un origen interno
cercano a la figura para limitar la cancelación; centroide e inercias en el sistema de
definición; ``Iyz = ∫yz dA``. La constante J no tiene método validado: ``NO_DISPONIBLE``.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.sections.catalog import PROFILE_SHAPES
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.validation import invalid_type, require_real

type Point = tuple[float, float]
type Contour = tuple[Point, ...]

TOLERANCE_RELATIVE: Final = 1e-9
"""Tolerancia geométrica relativa a la diagonal de la caja del contorno exterior."""
AREA_RELATIVE_MINIMUM: Final = 1e-12
"""Área mínima de un contorno, relativa al cuadrado de la diagonal."""
METHOD_POLYGON_V1: Final = "polygon-green/1"
"""Método de T-029 E1/E2: el Iyz es el de la fórmula de Green, sin ajuste (proyectos v7)."""
METHOD_POLYGON: Final = "polygon-green/2"
"""Método actual (T-031, TK-033): como /1, con el Iyz de ruido fijado a +0.0."""
IYZ_NOISE_RELATIVE: Final = 1e-12
"""|Iyz| ≤ 1e-12·√(Iy·Iz) es redondeo de la integración, no acoplamiento real (ADR-013)."""
METHOD_TORSION_FREEFORM: Final = "sin-solucion-exacta-validada/1"
_ORIGIN: Final = "calculado a partir de los vértices de esta revisión (croquis libre)"
_TORSION_MOTIVE: Final = (
    "sin método validado de la constante de torsión de Saint-Venant para un croquis libre "
    "(DEC-005; TK-028)"
)
_MIN_VERTICES: Final = 3


def _issue(key: str, field: str, params: dict[str, str | float] | None = None) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        key,
        {"field": field, **(params or {})},
        location=Location(field=field),
    )


def signed_area(contour: Sequence[Point]) -> float:
    """Área con signo (positiva si el contorno es antihorario), por la fórmula del cordón."""
    n = len(contour)
    return 0.5 * math.fsum(
        contour[i][0] * contour[(i + 1) % n][1] - contour[(i + 1) % n][0] * contour[i][1]
        for i in range(n)
    )


def _point_segment(p: Point, a: Point, b: Point) -> float:
    ax, az = b[0] - a[0], b[1] - a[1]
    length2 = ax * ax + az * az
    if length2 == 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * az) / length2))
    return math.hypot(p[0] - (a[0] + t * ax), p[1] - (a[1] + t * az))


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _cross(a, b, c), _cross(a, b, d)
    d3, d4 = _cross(c, d, a), _cross(c, d, b)
    return d1 * d2 < 0.0 and d3 * d4 < 0.0


def segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """Distancia mínima entre los segmentos ab y cd (0 si se cruzan)."""
    if _segments_cross(a, b, c, d):
        return 0.0
    return min(
        _point_segment(a, c, d),
        _point_segment(b, c, d),
        _point_segment(c, a, b),
        _point_segment(d, a, b),
    )


def point_in_polygon(p: Point, contour: Sequence[Point]) -> bool:
    """Punto interior por conteo de cruces de un rayo (válido si ``p`` no está en el borde)."""
    inside = False
    n = len(contour)
    for i in range(n):
        (y1, z1), (y2, z2) = contour[i], contour[(i + 1) % n]
        if (z1 > p[1]) != (z2 > p[1]) and p[0] < (y2 - y1) * (p[1] - z1) / (z2 - z1) + y1:
            inside = not inside
    return inside


def _contour_issues(
    contour: Contour, field: str, tol: float, minimum_area: float
) -> list[Diagnostic]:
    """Violaciones de un contorno aislado: tamaño, duplicados, área y autointersección."""
    n = len(contour)
    if n < _MIN_VERTICES:
        return [_issue("section.sketch_too_few_vertices", field, {"count": float(n)})]
    found: list[Diagnostic] = []
    for i in range(n):
        j = (i + 1) % n
        if math.hypot(contour[i][0] - contour[j][0], contour[i][1] - contour[j][1]) <= tol:
            found.append(_issue("section.sketch_duplicate_vertex", field, {"index": float(i)}))
    if found:
        return found
    for i in range(n):
        a, b = contour[i], contour[(i + 1) % n]
        c = contour[(i + 2) % n]
        if _point_segment(a, b, c) <= tol or _point_segment(c, a, b) <= tol:
            found.append(_issue("section.sketch_spike", field, {"index": float((i + 1) % n)}))
        for k in range(i + 2, n):
            if (k + 1) % n == i:
                continue  # aristas contiguas (cierre del contorno): ya tratadas como pico
            if segment_distance(a, b, contour[k], contour[(k + 1) % n]) <= tol:
                found.append(
                    _issue(
                        "section.sketch_self_intersection",
                        field,
                        {"edge_a": float(i), "edge_b": float(k)},
                    )
                )
    crossing = any(d.message_key == "section.sketch_self_intersection" for d in found)
    if not crossing and abs(signed_area(contour)) <= minimum_area:
        return [_issue("section.sketch_zero_area", field)]  # una línea: el pico es consecuencia
    return found


def _contours_distance(first: Contour, second: Contour) -> float:
    return min(
        segment_distance(
            first[i], first[(i + 1) % len(first)], second[k], second[(k + 1) % len(second)]
        )
        for i in range(len(first))
        for k in range(len(second))
    )


def geometry_tolerance(outer: Sequence[Point]) -> float:
    """Tolerancia absoluta (m) para este exterior: ``TOLERANCE_RELATIVE · diagonal``."""
    ys, zs = [p[0] for p in outer], [p[1] for p in outer]
    return TOLERANCE_RELATIVE * math.hypot(max(ys) - min(ys), max(zs) - min(zs))


def sketch_diagnostics(outer: Contour, holes: Sequence[Contour]) -> tuple[Diagnostic, ...]:
    """Todas las violaciones EDT-004 de un croquis (vacía si es válido).

    Los vértices ya son números finitos (``FreeformParameters`` y el borrador lo comprueban
    antes). Cada hueco se llama ``hole[i]`` y el exterior ``outer`` en el campo del diagnóstico.
    """
    tol = geometry_tolerance(outer) if len(outer) >= 1 else 0.0
    diagonal = tol / TOLERANCE_RELATIVE
    minimum_area = AREA_RELATIVE_MINIMUM * diagonal * diagonal
    found = _contour_issues(outer, "outer", tol, minimum_area)
    valid_holes: list[tuple[int, Contour]] = []
    for index, hole in enumerate(holes):
        issues = _contour_issues(hole, f"hole[{index}]", tol, minimum_area)
        found += issues
        if not issues:
            valid_holes.append((index, hole))
    if any(d.location is not None and d.location.field == "outer" for d in found):
        return tuple(found)
    for index, hole in valid_holes:
        field = f"hole[{index}]"
        if _contours_distance(outer, hole) <= tol:
            found.append(_issue("section.sketch_hole_touches_outer", field))
        elif not point_in_polygon(hole[0], outer):
            found.append(_issue("section.sketch_hole_outside", field))
    for position, (first_index, first) in enumerate(valid_holes):
        for second_index, second in valid_holes[position + 1 :]:
            if (
                _contours_distance(first, second) <= tol
                or point_in_polygon(first[0], second)
                or point_in_polygon(second[0], first)
            ):
                found.append(
                    _issue(
                        "section.sketch_holes_overlap",
                        f"hole[{second_index}]",
                        {"other": float(first_index)},
                    )
                )
    return tuple(found)


def shape_diagnostic(shape: object) -> Diagnostic | None:
    """Diagnóstico de una forma declarada no válida (``None`` si es válida o está sin declarar)."""
    if shape is None or (isinstance(shape, str) and shape in PROFILE_SHAPES):
        return None
    return _issue(
        "section.sketch_shape_invalid",
        "shape",
        {"shape": str(shape), "allowed": ", ".join(sorted(PROFILE_SHAPES))},
    )


def _canonical(contour: Contour, *, counterclockwise: bool) -> Contour:
    points = list(contour)
    if (signed_area(points) > 0.0) != counterclockwise:
        points.reverse()
    start = min(range(len(points)), key=lambda i: points[i])
    return tuple(points[start:] + points[:start])


def _point(value: object, field: str) -> Point:
    if not (isinstance(value, tuple | list) and len(value) == 2):  # noqa: PLR2004 (par y, z)
        raise invalid_type(field, "par (y, z)", value, Location(field=field))
    y = require_real(value[0], f"{field}.y") + 0.0  # −0,0 → +0,0: representación única
    z = require_real(value[1], f"{field}.z") + 0.0
    return (y, z)


def _contour(value: object, field: str) -> Contour:
    if not isinstance(value, tuple | list):
        raise invalid_type(field, "lista de vértices", value, Location(field=field))
    return tuple(_point(p, f"{field}[{i}]") for i, p in enumerate(value))


@dataclass(frozen=True, slots=True)
class FreeformParameters:
    """Exterior y huecos de un croquis libre, en forma canónica y ya validado (EDT-004)."""

    outer: Contour
    holes: tuple[Contour, ...] = ()
    shape: str | None = None
    """Forma declarada (una de ``PROFILE_SHAPES``) o ``None`` = sin declarar (T-031, TK-032)."""

    def __post_init__(self) -> None:
        problem = shape_diagnostic(self.shape)
        if problem is not None:
            raise InputValueError(problem)
        outer = _contour(self.outer, "outer")
        if not isinstance(self.holes, tuple | list):
            raise invalid_type("holes", "lista de contornos", self.holes)
        holes = tuple(_contour(h, f"hole[{i}]") for i, h in enumerate(self.holes))
        problems = sketch_diagnostics(outer, holes) if outer else ()
        if not outer:
            problems = (_issue("section.sketch_too_few_vertices", "outer", {"count": 0.0}),)
        if problems:
            raise InputValueError(problems[0])
        canonical_outer = _canonical(outer, counterclockwise=True)
        canonical_holes = tuple(sorted(_canonical(h, counterclockwise=False) for h in holes))
        # Lo guardado debe poder reabrirse: también se valida la forma canónica (los predicados
        # pueden diferir en ulps al cambiar el orden de los vértices).
        again = sketch_diagnostics(canonical_outer, canonical_holes)
        if again:
            raise InputValueError(again[0])
        object.__setattr__(self, "outer", canonical_outer)
        object.__setattr__(self, "holes", canonical_holes)


@dataclass(frozen=True, slots=True)
class PolygonGeometry:
    """Resultado de integrar el croquis: A, centroide e inercias centroidales."""

    area: float
    centroid_y: float
    centroid_z: float
    iy: float
    iz: float
    iyz: float


def _contour_moments(contour: Contour) -> tuple[float, float, float, float, float, float]:
    """(A, Sy, Sz, Iyy0, Izz0, Iyz0) con signo del contorno respecto al origen: ∫dA, ∫y, ∫z,
    ∫y², ∫z², ∫yz (positivos si es antihorario)."""
    n = len(contour)
    a_terms, sy, sz, yy, zz, yz = [], [], [], [], [], []
    for i in range(n):
        y0, z0 = contour[i]
        y1, z1 = contour[(i + 1) % n]
        c = y0 * z1 - y1 * z0
        a_terms.append(c)
        sy.append((y0 + y1) * c)
        sz.append((z0 + z1) * c)
        yy.append((y0 * y0 + y0 * y1 + y1 * y1) * c)
        zz.append((z0 * z0 + z0 * z1 + z1 * z1) * c)
        yz.append((y0 * z1 + 2.0 * y0 * z0 + 2.0 * y1 * z1 + y1 * z0) * c)
    return (
        0.5 * math.fsum(a_terms),
        math.fsum(sy) / 6.0,
        math.fsum(sz) / 6.0,
        math.fsum(yy) / 12.0,
        math.fsum(zz) / 12.0,
        math.fsum(yz) / 24.0,
    )


def polygon_geometry(parameters: FreeformParameters) -> PolygonGeometry:
    """A, centroide e Iy = ∫z², Iz = ∫y², Iyz = ∫yz centroidales del croquis (exterior − huecos).

    Los vértices se trasladan a la media del exterior antes de integrar (la cancelación no
    depende de dónde se dibujó la figura) y el resultado se devuelve en el sistema original.
    """
    oy = math.fsum(p[0] for p in parameters.outer) / len(parameters.outer)
    oz = math.fsum(p[1] for p in parameters.outer) / len(parameters.outer)

    def shifted(contour: Contour) -> Contour:
        return tuple((p[0] - oy, p[1] - oz) for p in contour)

    # Exterior antihorario (momentos positivos) y huecos horarios (negativos): se suman tal cual.
    moments = [_contour_moments(shifted(c)) for c in (parameters.outer, *parameters.holes)]
    total = [math.fsum(m[k] for m in moments) for k in range(6)]
    area, sy, sz, i_yy, i_zz, i_yz = total
    cy, cz = sy / area, sz / area
    return PolygonGeometry(
        area=area,
        centroid_y=oy + cy,
        centroid_z=oz + cz,
        iy=i_zz - area * cz * cz,
        iz=i_yy - area * cy * cy,
        iyz=i_yz - area * cy * cz,
    )


def freeform_properties(parameters: FreeformParameters, *, snap: bool = True) -> SectionProperties:
    """Propiedades CALCULADO de un croquis libre; J ``NO_DISPONIBLE`` con motivo.

    ``snap=True`` (método ``polygon-green/2``) fija a +0.0 el Iyz que es ruido de redondeo
    (|Iyz| ≤ ``IYZ_NOISE_RELATIVE``·√(Iy·Iz)); ``snap=False`` reproduce el método ``/1`` de los
    proyectos v7 ya guardados, para poder seguir contrastándolos (antimanipulación).
    """
    geometry = polygon_geometry(parameters)
    method = METHOD_POLYGON if snap else METHOD_POLYGON_V1
    iyz = geometry.iyz
    if snap and abs(iyz) <= IYZ_NOISE_RELATIVE * math.sqrt(geometry.iy * geometry.iz):
        iyz = 0.0

    def calc(value: float, unit: str) -> SectionProperty:
        return SectionProperty(value + 0.0, unit, _ORIGIN, method, PropertyStatus.CALCULADO)

    return SectionProperties(
        area=calc(geometry.area, "m²"),
        centroid_y=calc(geometry.centroid_y, "m"),
        centroid_z=calc(geometry.centroid_z, "m"),
        iy=calc(geometry.iy, "m⁴"),
        iz=calc(geometry.iz, "m⁴"),
        iyz=calc(iyz, "m⁴"),
        torsion_constant=SectionProperty(
            None, "m⁴", _TORSION_MOTIVE, METHOD_TORSION_FREEFORM, PropertyStatus.NO_DISPONIBLE
        ),
    )


@dataclass(frozen=True, slots=True)
class PrincipalAxes:
    """Ejes principales centroidales, con la convención de ADR-001 (la inercia de un eje es la
    integral del cuadrado de la coordenada perpendicular, como Iy = ∫z²).

    ``angle`` (rad, de +y hacia +z, en (−π/2, π/2]) es el del eje respecto al cual la inercia es
    ``i_max``; el eje perpendicular tiene la inercia ``i_min``.
    """

    angle: float
    i_max: float
    i_min: float


def principal_axes(iy: float, iz: float, iyz: float) -> PrincipalAxes:
    """Ejes principales a partir de Iy = ∫z², Iz = ∫y², Iyz = ∫yz (derivados, no se guardan).

    Exige entradas finitas y un tensor de inercia admisible (Iy, Iz > 0 e Iyz² ≤ Iy·Iz).
    """
    for name, number in (("Iy", iy), ("Iz", iz), ("Iyz", iyz)):
        require_real(number, name)
    if not (iy > 0.0 and iz > 0.0 and iyz * iyz <= iy * iz):
        raise InputValueError(_issue("section.principal_axes_invalid", "inertia"))
    mean = 0.5 * (iy + iz)
    radius = math.hypot(0.5 * (iz - iy), iyz)
    i_max = mean + radius
    # ∫u² máximo en 2θ = atan2(2Iyz, Iz − Iy): la inercia respecto a ese eje es la MÍNIMA, y la
    # máxima corresponde al eje perpendicular (+π/2). «+ 0.0» elimina el cero negativo.
    angle = 0.5 * math.atan2(2.0 * iyz + 0.0, iz - iy) + 0.5 * math.pi
    if angle > 0.5 * math.pi:
        angle -= math.pi
    return PrincipalAxes(angle=angle, i_max=i_max, i_min=(iy * iz - iyz * iyz) / i_max)
