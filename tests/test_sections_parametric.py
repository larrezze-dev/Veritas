"""Fórmulas de I, RHS, SHS y CHS frente a referencias independientes (plan T-025 E1, D13).

Tres niveles: (1) las fórmulas de la SRS (anillo) y las simetrías; (2) la cuadratura de acordes de
``section_reference`` (Python puro, contorno «caja menos huecos», sin llamar a las fórmulas), y los
racionales exactos (``fractions``) y 60 dígitos (``decimal``) para pared fina; (3) invariancias
(escala, intercambio b↔h, límites, monotonía, 0 < Av < A, ∫Q dz = Iy).

Tolerancias: medidas en esta máquina (~1e-15 contra la cuadratura, 1e-14 contra racionales) y
fijadas con margen de unas 10³ veces.
"""

import math
from decimal import Decimal, getcontext
from fractions import Fraction

import pytest

import section_reference as ref
from veritas.core.sections.parametric import (
    ParametricParameters,
    ParametricShape,
    inner_radius,
)
from veritas.core.sections.parametric_properties import parametric_properties
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.sections.shear import parametric_shear, rectangular_shear

TOL_QUADRATURE = 1e-12
TOL_SHEAR = 1e-11
TOL_EXACT = 1e-13

I_CASES = [
    (0.300, 0.150, 0.0071, 0.0107, 0.0),
    (0.300, 0.150, 0.0071, 0.0107, 0.0150),
    (0.300, 0.150, 0.0071, 0.0107, 0.0714),  # r casi en la cota (b − tw)/2 = 0,07145
    (0.200, 0.200, 0.0200, 0.0300, 0.0),
    (0.100, 0.300, 0.0400, 0.0100, 0.0050),  # ala ancha y alma gruesa
]
RHS_CASES = [
    (0.300, 0.200, 0.010, 0.0),
    (0.300, 0.200, 0.010, 0.005),  # ro < t: interior de arista viva
    (0.300, 0.200, 0.010, 0.010),  # ro = t: continuidad
    (0.300, 0.200, 0.010, 0.025),  # ro > t: esquinas concéntricas
    (0.300, 0.200, 0.010, 0.100),  # ro = b/2: lado menor semicircular
    (0.200, 0.200, 0.080, 0.050),  # pared gruesa
]
CHS_CASES = [(0.100, 0.005), (0.100, 0.020), (0.100, 0.049), (0.500, 0.0125)]


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


def props(shape: ParametricShape, **dims: float) -> SectionProperties:
    return parametric_properties(ParametricParameters.from_values(shape, dims))


def value(prop: SectionProperty) -> float:
    assert prop.value is not None
    return float(prop.value)


# --- (2) cuadratura independiente --------------------------------------------------------


@pytest.mark.parametrize(("h", "b", "tw", "tf", "r"), I_CASES)
def test_i_matches_the_independent_quadrature(
    h: float, b: float, tw: float, tf: float, r: float
) -> None:
    p = props(ParametricShape.I, h=h, b=b, tw=tw, tf=tf, r=r)
    g = ref.i_section(h, b, tw, tf, r)
    assert rel(value(p.area), g.area()) < TOL_QUADRATURE
    assert rel(value(p.iy), g.iy()) < TOL_QUADRATURE
    assert rel(value(p.iz), g.iz()) < TOL_QUADRATURE
    assert abs(g.iyz()) < 1e-18  # el contorno es simétrico: ∫yz dA = 0


@pytest.mark.parametrize(("h", "b", "t", "ro"), RHS_CASES)
def test_rhs_matches_the_independent_quadrature(h: float, b: float, t: float, ro: float) -> None:
    p = props(ParametricShape.RHS, h=h, b=b, t=t, ro=ro)
    g = ref.rect_tube(h, b, t, ro)
    assert rel(value(p.area), g.area()) < TOL_QUADRATURE
    assert rel(value(p.iy), g.iy()) < TOL_QUADRATURE
    assert rel(value(p.iz), g.iz()) < TOL_QUADRATURE
    assert abs(g.iyz()) < 1e-18


@pytest.mark.parametrize(("b", "t", "ro"), [(0.2, 0.01, 0.0), (0.2, 0.01, 0.03), (0.2, 0.08, 0.1)])
def test_shs_is_the_rhs_with_h_equal_b(b: float, t: float, ro: float) -> None:
    shs = props(ParametricShape.SHS, b=b, t=t, ro=ro)
    rhs = props(ParametricShape.RHS, h=b, b=b, t=t, ro=ro)
    for name in ("area", "iy", "iz"):
        assert value(getattr(shs, name)) == value(getattr(rhs, name))
    assert value(shs.iy) == value(shs.iz)  # Iy = Iz: ejes principales indeterminados
    assert shs.area.method == "shs-closed-form/1"
    assert rhs.area.method == "rhs-closed-form/1"
    g = ref.rect_tube(b, b, t, ro)
    assert rel(value(shs.iy), g.iy()) < TOL_QUADRATURE


@pytest.mark.parametrize(("d", "t"), CHS_CASES)
def test_chs_matches_the_independent_quadrature_and_the_srs_ring(d: float, t: float) -> None:
    p = props(ParametricShape.CHS, d=d, t=t)
    g = ref.circular_tube(d, t)
    assert rel(value(p.area), g.area()) < TOL_QUADRATURE
    assert rel(value(p.iy), g.iy()) < TOL_QUADRATURE
    assert rel(value(p.iz), g.iz()) < TOL_QUADRATURE
    big, small = d / 2.0, d / 2.0 - t
    # SRS: anillo A = π(R² − r²), I = π(R⁴ − r⁴)/4 (la J del anillo es Ip = 2·I).
    assert rel(value(p.area), math.pi * (big**2 - small**2)) < 1e-12
    assert rel(value(p.iy), math.pi * (big**4 - small**4) / 4.0) < 1e-12
    assert value(p.iy) == value(p.iz)
    assert rel(value(p.torsion_constant), math.pi * (big**4 - small**4) / 2.0) < 1e-12
    assert p.torsion_constant.method == "annulus-torsion-exact/1"


def test_centroid_and_product_of_inertia_are_exactly_positive_zero() -> None:
    for shape, dims in (
        (ParametricShape.I, {"h": 0.3, "b": 0.15, "tw": 0.007, "tf": 0.01, "r": 0.01}),
        (ParametricShape.RHS, {"h": 0.3, "b": 0.2, "t": 0.01, "ro": 0.02}),
        (ParametricShape.SHS, {"b": 0.2, "t": 0.01}),
        (ParametricShape.CHS, {"d": 0.1, "t": 0.005}),
    ):
        p = props(shape, **dims)
        for name in ("centroid_y", "centroid_z", "iyz"):
            prop = getattr(p, name)
            assert prop.value == 0.0
            assert math.copysign(1.0, prop.value) > 0.0
            assert prop.status is PropertyStatus.CALCULADO


# --- Pared fina sin pérdida de cifras (I3) ----------------------------------------------


def exact_i_sharp(h: float, b: float, tw: float, tf: float) -> tuple[Fraction, Fraction, Fraction]:
    """Caja menos dos huecos, en racionales exactos (otra descomposición)."""
    h_, b_, tw_, tf_ = (Fraction(v) for v in (h, b, tw, tf))
    hw, c = h_ - 2 * tf_, (b_ - tw_) / 2
    x = tw_ / 2 + c / 2
    area = b_ * h_ - 2 * c * hw
    iy = b_ * h_**3 / 12 - 2 * c * hw**3 / 12
    iz = h_ * b_**3 / 12 - 2 * (hw * c**3 / 12 + hw * c * x * x)
    return area, iy, iz


def exact_rhs_sharp(h: float, b: float, t: float) -> tuple[Fraction, Fraction, Fraction]:
    h_, b_, t_ = Fraction(h), Fraction(b), Fraction(t)
    hi, bi = h_ - 2 * t_, b_ - 2 * t_
    return b_ * h_ - bi * hi, (b_ * h_**3 - bi * hi**3) / 12, (h_ * b_**3 - hi * bi**3) / 12


@pytest.mark.parametrize("slender", [1e-3, 1e-5, 1e-6])
def test_thin_wall_i_keeps_its_digits(slender: float) -> None:
    h, b = 0.3, 0.15
    tw, tf = h * slender, h * slender
    p = props(ParametricShape.I, h=h, b=b, tw=tw, tf=tf)
    for got, want in zip((p.area, p.iy, p.iz), exact_i_sharp(h, b, tw, tf), strict=True):
        assert rel(value(got), float(want)) < TOL_EXACT


@pytest.mark.parametrize("slender", [1e-3, 1e-5, 1e-6])
def test_thin_wall_rhs_keeps_its_digits(slender: float) -> None:
    h, b = 0.3, 0.2
    t = h * slender
    p = props(ParametricShape.RHS, h=h, b=b, t=t)
    for got, want in zip((p.area, p.iy, p.iz), exact_rhs_sharp(h, b, t), strict=True):
        assert rel(value(got), float(want)) < TOL_EXACT


def _pi() -> Decimal:
    """π a 60 dígitos (fórmula de Machin) para la referencia de la CHS fina."""
    getcontext().prec = 70

    def arctan_inv(n: int) -> Decimal:
        x, total, term, k = Decimal(1) / n, Decimal(0), Decimal(1) / n, 0
        while abs(term) > Decimal(10) ** -68:
            total += term / (2 * k + 1)
            term = -term * x * x
            k += 1
        return total

    return 16 * arctan_inv(5) - 4 * arctan_inv(239)


@pytest.mark.parametrize("slender", [1e-3, 1e-5, 1e-6])
def test_thin_chs_keeps_its_digits(slender: float) -> None:
    d = 0.5
    t = d * slender
    pi = _pi()
    big, small = Decimal(d) / 2, Decimal(d) / 2 - Decimal(t)  # exterior − interior a 60 dígitos
    area = pi * (big**2 - small**2)
    inertia = pi * (big**4 - small**4) / 4
    p = props(ParametricShape.CHS, d=d, t=t)
    assert rel(value(p.area), float(area)) < TOL_EXACT
    assert rel(value(p.iy), float(inertia)) < TOL_EXACT
    assert rel(value(p.torsion_constant), float(2 * inertia)) < TOL_EXACT


# --- (3) invariancias ---------------------------------------------------------------------


def test_scale_invariance() -> None:
    lam = 3.7
    base = props(ParametricShape.I, h=0.3, b=0.15, tw=0.007, tf=0.01, r=0.01)
    big = props(
        ParametricShape.I, h=0.3 * lam, b=0.15 * lam, tw=0.007 * lam, tf=0.01 * lam, r=0.01 * lam
    )
    assert rel(value(big.area), value(base.area) * lam**2) < 1e-12
    assert rel(value(big.iy), value(base.iy) * lam**4) < 1e-12
    assert rel(value(big.iz), value(base.iz) * lam**4) < 1e-12
    shear = parametric_shear(
        ParametricParameters.from_values(ParametricShape.RHS, {"h": 0.3, "b": 0.2, "t": 0.01})
    ).shear_z
    scaled = parametric_shear(
        ParametricParameters.from_values(
            ParametricShape.RHS, {"h": 0.3 * lam, "b": 0.2 * lam, "t": 0.01 * lam}
        )
    ).shear_z
    assert rel(value(scaled), value(shear) * lam**2) < 1e-12
    c1 = props(ParametricShape.CHS, d=0.1, t=0.005)
    c2 = props(ParametricShape.CHS, d=0.1 * lam, t=0.005 * lam)
    assert rel(value(c2.torsion_constant), value(c1.torsion_constant) * lam**4) < 1e-12


def test_rhs_swapping_b_and_h_swaps_iy_and_iz() -> None:
    a = props(ParametricShape.RHS, h=0.3, b=0.2, t=0.01, ro=0.02)
    b = props(ParametricShape.RHS, h=0.2, b=0.3, t=0.01, ro=0.02)
    assert rel(value(a.iy), value(b.iz)) < 1e-14
    assert rel(value(a.iz), value(b.iy)) < 1e-14
    assert rel(value(a.area), value(b.area)) < 1e-14


def test_rhs_area_is_continuous_at_ro_equal_t() -> None:
    t = 0.01
    low = props(ParametricShape.RHS, h=0.3, b=0.2, t=t, ro=t * (1 - 1e-9))
    high = props(ParametricShape.RHS, h=0.3, b=0.2, t=t, ro=t * (1 + 1e-9))
    for name in ("area", "iy", "iz"):
        assert rel(value(getattr(low, name)), value(getattr(high, name))) < 1e-8


def test_inner_radius_is_derived_and_concentric() -> None:
    assert inner_radius(0.005, 0.01) == 0.0
    assert inner_radius(0.025, 0.01) == pytest.approx(0.015, abs=1e-15)
    assert (
        "ri"
        not in ParametricParameters.from_values(
            ParametricShape.RHS, {"h": 0.3, "b": 0.2, "t": 0.01, "ro": 0.025}
        ).as_dict()
    )


def test_corner_radius_monotonicity() -> None:
    iy_rhs = [
        value(props(ParametricShape.RHS, h=0.3, b=0.2, t=0.01, ro=r).iy)
        for r in (0.0, 0.01, 0.03, 0.1)
    ]
    assert iy_rhs == sorted(iy_rhs, reverse=True)  # más radio exterior, menos material
    area_i = [
        value(props(ParametricShape.I, h=0.3, b=0.15, tw=0.007, tf=0.01, r=r).area)
        for r in (0.0, 0.005, 0.02)
    ]
    assert area_i == sorted(area_i)  # más acuerdo, más material


def test_i_with_web_filling_the_flange_tends_to_the_rectangle() -> None:
    b, h = 0.15, 0.3
    for eps in (1e-3, 1e-6, 1e-9):
        p = props(ParametricShape.I, h=h, b=b, tw=b * (1 - eps), tf=0.01)
        assert rel(value(p.area), b * h) < 2 * eps
        assert rel(value(p.iy), b * h**3 / 12.0) < 2 * eps
        assert rel(value(p.iz), h * b**3 / 12.0) < 4 * eps  # Iz ∝ tw³: converge con 3ε


def test_chs_with_wall_to_half_diameter_tends_to_the_solid_circle() -> None:
    d = 0.1
    for eps in (1e-3, 1e-6, 1e-9):
        p = props(ParametricShape.CHS, d=d, t=d / 2 * (1 - eps))
        assert rel(value(p.area), math.pi * d**2 / 4.0) < 3 * eps
        assert rel(value(p.iy), math.pi * d**4 / 64.0) < 5 * eps
        assert rel(value(p.torsion_constant), math.pi * d**4 / 32.0) < 5 * eps


@pytest.mark.parametrize("radius", [1e-3, 1e-5, 1e-7])
def test_radii_tend_to_the_sharp_edge_shape_quadratically(radius: float) -> None:
    """r → 0 (I) y ro → 0 (RHS) convergen a la forma de aristas vivas con error O(r²)."""
    i0 = props(ParametricShape.I, h=0.3, b=0.15, tw=0.0071, tf=0.0107)
    i1 = props(ParametricShape.I, h=0.3, b=0.15, tw=0.0071, tf=0.0107, r=radius)
    rhs0 = props(ParametricShape.RHS, h=0.3, b=0.2, t=0.01)
    rhs1 = props(ParametricShape.RHS, h=0.3, b=0.2, t=0.01, ro=radius)
    for sharp, rounded in ((i0, i1), (rhs0, rhs1)):
        for name in ("area", "iy", "iz"):
            assert rel(value(getattr(rounded, name)), value(getattr(sharp, name))) < 1e3 * radius**2


def test_thin_rhs_tends_to_the_thin_wall_perimeter_rule() -> None:
    h, b, t = 0.3, 0.2, 1e-7
    p = props(ParametricShape.RHS, h=h, b=b, t=t)
    assert rel(value(p.area), 2 * t * (b + h)) < 1e-5


# --- Torsión: solo donde hay solución exacta (D7) -----------------------------------------


@pytest.mark.parametrize(
    ("shape", "dims"),
    [
        (ParametricShape.I, {"h": 0.3, "b": 0.15, "tw": 0.007, "tf": 0.01}),
        (ParametricShape.RHS, {"h": 0.3, "b": 0.2, "t": 0.01}),
        (ParametricShape.SHS, {"b": 0.2, "t": 0.01, "ro": 0.02}),
    ],
)
def test_torsion_is_unavailable_with_a_reason_where_there_is_no_exact_solution(
    shape: ParametricShape, dims: dict[str, float]
) -> None:
    j = props(shape, **dims).torsion_constant
    assert j.status is PropertyStatus.NO_DISPONIBLE
    assert j.value is None  # nunca 0
    assert "sin solución exacta validada" in j.origin
    assert j.method == "sin-solucion-exacta-validada/1"


# --- Área de cortante energética ---------------------------------------------------------


def test_rectangle_shear_is_five_sixths_of_the_area_in_both_axes() -> None:
    shear = rectangular_shear(0.2, 0.4)
    assert shear.shear_y.value == shear.shear_z.value == pytest.approx(5.0 / 6.0 * 0.08, rel=1e-15)
    g = ref.rectangle(0.2, 0.4)
    assert rel(g.shear_area_z(), 5.0 / 6.0 * 0.08) < 1e-13  # la cuadratura reproduce el 5/6


@pytest.mark.parametrize(
    ("h", "b", "tw", "tf"),
    [
        (0.3, 0.15, 0.0071, 0.0107),
        (0.2, 0.2, 0.02, 0.03),
        (0.1, 0.3, 0.04, 0.01),
        (0.5, 0.01, 0.005, 0.002),
    ],
)
def test_i_shear_matches_the_quadrature_of_the_same_definition(
    h: float, b: float, tw: float, tf: float
) -> None:
    shear = parametric_shear(
        ParametricParameters.from_values(ParametricShape.I, {"h": h, "b": b, "tw": tw, "tf": tf})
    )
    g = ref.i_section(h, b, tw, tf)
    assert rel(value(shear.shear_z), g.shear_area_z()) < TOL_SHEAR
    area = 2 * b * tf + (h - 2 * tf) * tw
    assert 0.0 < value(shear.shear_z) < area  # Cauchy–Schwarz
    # ∫ Q dz = Iy: la identidad que sostiene la cota.
    assert rel(g.total(g.first_moment_above), g.iy()) < 1e-11


@pytest.mark.parametrize(("h", "b", "t"), [(0.3, 0.2, 0.01), (0.2, 0.2, 0.08), (0.1, 0.4, 0.02)])
def test_rhs_shear_matches_the_quadrature_of_the_same_definition(
    h: float, b: float, t: float
) -> None:
    shear = parametric_shear(
        ParametricParameters.from_values(ParametricShape.RHS, {"h": h, "b": b, "t": t})
    )
    g = ref.rect_tube(h, b, t)
    assert rel(value(shear.shear_z), g.shear_area_z()) < TOL_SHEAR
    assert 0.0 < value(shear.shear_z) < 2 * t * (b + h - 2 * t)


def test_sharp_rhs_equals_an_i_with_double_web_and_same_flange() -> None:
    """Con cortes rectos, el RHS de aristas vivas es una I con tw = 2t y tf = t (invariancia)."""
    h, b, t = 0.3, 0.2, 0.01
    rhs = props(ParametricShape.RHS, h=h, b=b, t=t)
    i = props(ParametricShape.I, h=h, b=b, tw=2 * t, tf=t)
    assert rel(value(rhs.area), value(i.area)) < 1e-14
    assert rel(value(rhs.iy), value(i.iy)) < 1e-14
    shear_rhs = parametric_shear(
        ParametricParameters.from_values(ParametricShape.RHS, {"h": h, "b": b, "t": t})
    ).shear_z
    shear_i = parametric_shear(
        ParametricParameters.from_values(ParametricShape.I, {"h": h, "b": b, "tw": 2 * t, "tf": t})
    ).shear_z
    assert rel(value(shear_rhs), value(shear_i)) < 1e-13


def test_shear_scope_is_exactly_the_approved_one() -> None:
    def statuses(shape: ParametricShape, **dims: float) -> tuple[PropertyStatus, PropertyStatus]:
        shear = parametric_shear(ParametricParameters.from_values(shape, dims))
        return shear.shear_y.status, shear.shear_z.status

    calc, none = PropertyStatus.CALCULADO, PropertyStatus.NO_DISPONIBLE
    i = {"h": 0.3, "b": 0.15, "tw": 0.007, "tf": 0.01}
    assert statuses(ParametricShape.I, **i) == (none, calc)
    assert statuses(ParametricShape.I, **i, r=0.005) == (none, none)
    rhs = {"h": 0.3, "b": 0.2, "t": 0.01}
    assert statuses(ParametricShape.RHS, **rhs) == (calc, calc)
    assert statuses(ParametricShape.RHS, **rhs, ro=0.01) == (none, none)
    assert statuses(ParametricShape.SHS, b=0.2, t=0.01) == (calc, calc)
    assert statuses(ParametricShape.SHS, b=0.2, t=0.01, ro=0.01) == (none, none)
    assert statuses(ParametricShape.CHS, d=0.1, t=0.005) == (calc, calc)


@pytest.mark.parametrize(("h", "b", "t"), [(0.3, 0.2, 0.01), (0.2, 0.2, 0.08), (0.1, 0.4, 0.02)])
def test_rhs_avy_is_avz_with_b_and_h_swapped(h: float, b: float, t: float) -> None:
    shear = parametric_shear(
        ParametricParameters.from_values(ParametricShape.RHS, {"h": h, "b": b, "t": t})
    )
    swapped = parametric_shear(
        ParametricParameters.from_values(ParametricShape.RHS, {"h": b, "b": h, "t": t})
    )
    assert rel(value(shear.shear_y), value(swapped.shear_z)) < 1e-14
    assert rel(value(shear.shear_y), ref.rect_tube(b, h, t).shear_area_z()) < TOL_SHEAR
    square = parametric_shear(
        ParametricParameters.from_values(ParametricShape.SHS, {"b": b, "t": t / 2})
    )
    assert rel(value(square.shear_y), value(square.shear_z)) < 1e-14


@pytest.mark.parametrize(("d", "t"), [(0.1, 0.005), (0.1, 0.0499), (1.0, 0.001), (0.3, 0.02)])
def test_chs_shear_closed_form_matches_the_chord_quadrature(d: float, t: float) -> None:
    shear = parametric_shear(
        ParametricParameters.from_values(ParametricShape.CHS, {"d": d, "t": t})
    )
    g = ref.circular_tube(d, t)
    tol = 1e-9 if d / t > 500 else TOL_SHEAR
    assert rel(value(shear.shear_z), g.shear_area_z()) < tol
    assert value(shear.shear_y) == value(shear.shear_z)
    area = props(ParametricShape.CHS, d=d, t=t).area
    assert 0.0 < value(shear.shear_z) < value(area)


def test_unavailable_shear_carries_a_reason_and_never_zero() -> None:
    shear = parametric_shear(
        ParametricParameters.from_values(ParametricShape.CHS, {"d": 0.1, "t": 0.005})
    )
    for prop in (shear.shear_y, shear.shear_z):
        assert prop.status is PropertyStatus.CALCULADO
        assert prop.method == "shear-energy-straight-cut/1"
    radii = parametric_shear(
        ParametricParameters.from_values(
            ParametricShape.I, {"h": 0.3, "b": 0.15, "tw": 0.007, "tf": 0.01, "r": 0.01}
        )
    )
    assert "radios de acuerdo" in radii.shear_z.origin
