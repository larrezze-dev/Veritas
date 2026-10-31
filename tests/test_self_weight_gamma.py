"""Peso específico γ y selección de barras del peso propio (LD-003; plan T-019, E1).

Referencia independiente (a mano, sintética, sin valores del CTE): γ = 25 kN/m³,
b = 0,2 m, h = 0,4 m, L = 2 m → A = 0,08 m², q = 2000 N/m y F = 4000 N.
Tolerancia relativa 1e-12: pocos productos en doble precisión.
"""

from typing import Any
from uuid import uuid4

import pytest

from veritas import cli
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.entities import Member
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCase, LoadCategory
from veritas.core.loads.loads import SelfWeight, SelfWeightMode
from veritas.core.materials.material import MaterialFamily
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.translator import translate_spatial

D = DiagnosticCode
GAMMA = Quantity.of(25, "kN/m³")


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def build(
    specific_weight: Quantity | None = GAMMA, members: int = 1
) -> tuple[StructuralModel, list[Member], LoadCase]:
    model = StructuralModel()
    material = model.add_material(
        name="Sintético",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(1000, "kg/m³"),
        specific_weight=specific_weight,
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    found: list[Member] = []
    for index in range(members):
        a = model.add_node(m(0), m(0), m(index))
        b = model.add_node(m(2), m(0), m(index))
        found.append(model.add_member(a.id, b.id, material=material.ref, section=section.ref))
    case = model.add_load_case("G1", LoadCategory.PERMANENT)
    return model, found, case


def test_specific_weight_reference() -> None:
    model, (member,), case = build()
    model.set_self_weight(case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT)
    assert model.self_weight_load(member.id)[2].to("N/m") == pytest.approx(-2000.0, rel=1e-12)
    resultant = model.case_resultant(case.id)
    assert resultant.force[2].to("N") == pytest.approx(-4000.0, rel=1e-12)
    assert resultant.moment[1].to("N·m") == pytest.approx(4000.0, rel=1e-12)  # r = (1, 0, 0)


def test_gamma_material_with_mass_density_mode_is_unchanged() -> None:
    """Un γ declarado no cambia el modo por defecto: sigue siendo ρ·A·g, bit a bit."""
    with_gamma, (m1,), c1 = build()
    without, (m2,), c2 = build(specific_weight=None)
    with_gamma.set_self_weight(c1.id, gravity=Quantity.of(10, "m/s²"))
    without.set_self_weight(c2.id, gravity=Quantity.of(10, "m/s²"))
    assert repr(with_gamma.self_weight_load(m1.id)) == repr(without.self_weight_load(m2.id))
    assert with_gamma.self_weight_load(m1.id)[2].to("N/m") == pytest.approx(-800.0, rel=1e-12)


def test_material_specific_weight_validation_and_revision() -> None:
    model, (member,), _ = build()
    assert model.material(member.material).specific_weight == 25e3
    for bad in (Quantity.of(0, "N/m³"), Quantity.of(-1, "kN/m³")):
        with pytest.raises(InputValueError) as info:
            build(specific_weight=bad)
        assert info.value.diagnostic.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputValueError) as info:
        build(specific_weight=Quantity.of(1, "kg/m³"))
    assert info.value.diagnostic.code is D.UNITS_INCOMPATIBLE


def test_revise_material_keep_set_and_clear_specific_weight() -> None:
    model = StructuralModel()
    kwargs: dict[str, Any] = {
        "name": "A",
        "family": MaterialFamily.STEEL,
        "source": "test",
        "elastic_modulus": Quantity.of(210, "GPa"),
        "shear_modulus": Quantity.of(81, "GPa"),
        "density": Quantity.of(1000, "kg/m³"),
    }
    material = model.add_material(**kwargs)
    assert material.specific_weight is None
    r2 = model.revise_material(material.id, specific_weight=GAMMA)
    assert (r2.revision, r2.specific_weight) == (2, 25e3)
    assert model.revise_material(material.id, name="B").specific_weight == 25e3  # KEEP
    assert model.revise_material(material.id, specific_weight=GAMMA).revision == 3  # nombre B
    cleared = model.revise_material(material.id, specific_weight=None)
    assert cleared.specific_weight is None


def test_gravity_must_be_omitted_in_specific_weight_mode() -> None:
    model, _, case = build()
    with pytest.raises(InputValueError) as info:
        model.set_self_weight(
            case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT, gravity=Quantity.of(10, "m/s²")
        )
    assert info.value.diagnostic.message_key == "load.self_weight_mode_gravity"
    assert model.self_weight is None
    with pytest.raises(InputTypeError):
        SelfWeight(uuid4(), 1, uuid4(), None)  # MASS_DENSITY exige g


def test_member_without_gamma_fails_explicitly() -> None:
    model, (member,), case = build(specific_weight=None)
    revision = model.model_revision
    model.set_self_weight(case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT)
    assert model.model_revision == revision + 1
    with pytest.raises(InputValueError) as info:
        model.self_weight_load(member.id)
    assert info.value.diagnostic.code is D.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "material.specific_weight_missing"
    with pytest.raises(InputValueError):  # el equilibrio de la entrada tampoco lo omite
        model.case_resultant(case.id)


def test_exclusion_with_two_members() -> None:
    model, (first, second), case = build(members=2)
    model.set_self_weight(case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT, excluded_members=[first.id])
    assert set(model.self_weight_loads()) == {second.id}
    assert model.case_resultant(case.id).force[2].to("N") == pytest.approx(-4000.0, rel=1e-12)
    with pytest.raises(InputValueError) as info:
        model.self_weight_load(first.id)
    assert info.value.diagnostic.message_key == "load.self_weight_member_excluded"


def test_exclusion_rules() -> None:
    model, (first, second), case = build(members=2)
    with pytest.raises(InputValueError) as info:
        model.set_self_weight(case.id, excluded_members=[uuid4()])
    assert info.value.diagnostic.code is D.REFERENCE_NOT_FOUND
    with pytest.raises(InputValueError) as info:
        model.set_self_weight(case.id, excluded_members=[first.id, second.id])
    assert info.value.diagnostic.message_key == "load.self_weight_all_excluded"
    assert model.self_weight is None


def test_excluded_member_is_not_derived() -> None:
    """La barra excluida no exige γ: aunque su material no lo tenga, no falla."""
    model, (first, second), case = build(members=2)
    plain = model.add_material(
        name="Sin γ",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(1000, "kg/m³"),
    )
    model.assign_material(second.id, plain.ref)
    model.set_self_weight(
        case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT, excluded_members=[second.id]
    )
    assert set(model.self_weight_loads()) == {first.id}


def test_new_member_is_included_by_default_and_revision_rules() -> None:
    model, (first, second), case = build(members=2)
    sw = model.set_self_weight(case.id, excluded_members=[first.id])
    assert model.set_self_weight(case.id, excluded_members=[first.id]) is sw  # idéntico
    revision = model.model_revision
    revised = model.set_self_weight(case.id, excluded_members=[second.id])
    assert (revised.id, revised.revision) == (sw.id, 2)
    assert model.model_revision == revision + 1
    a = model.add_node(m(5), m(0), m(0))
    b = model.add_node(m(6), m(0), m(0))
    material = model.member(first.id).material
    section = model.member(first.id).section
    third = model.add_member(a.id, b.id, material=material, section=section)
    assert set(model.self_weight_loads()) == {first.id, third.id}


def test_default_set_self_weight_is_unchanged() -> None:
    model, _, case = build()
    sw = model.set_self_weight(case.id)
    assert (sw.mode, sw.excluded_members) == (SelfWeightMode.MASS_DENSITY, frozenset())
    assert sw.gravity == 9.80665


def test_translators_apply_gamma_and_exclusions() -> None:
    """E2: los dos traductores aplican γ y exclusiones (ya no se rechazan como en E1)."""
    model, (first, second), case = build(members=2)
    model.set_self_weight(case.id, mode=SelfWeightMode.SPECIFIC_WEIGHT, excluded_members=[first.id])
    planar = translate_planar_xz(model, AnalysisConfiguration(case.id)).request
    spatial = translate_spatial(model, SpatialAnalysisConfiguration(case.id)).request
    for request in (planar, spatial):
        (beam,) = [b for b in request.beams if b.entity_id == str(second.id)]
        assert [load.beam for load in request.beam_loads] == [beam.tag]
    assert planar.beam_loads[0].wy == pytest.approx(-2000.0, rel=1e-12)


SELFWEIGHT = ["load", "selfweight", "--b", "0.2", "--h", "0.4", "--length", "2 m"]


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_gamma(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run([*SELFWEIGHT, "--gamma", "25 kN/m3"], capsys)
    assert (code, err) == (cli.EXIT_OK, "")
    assert "q = gamma·A = 2000.000 N/m  ->  qz = -2000.000 N/m" in out
    assert "Fz = -q·L = -4000.000 N" in out
    assert "gamma = 25000.000 N/m³" in out
    assert "g =" not in out


def test_cli_gamma_profile_kn_m(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run([*SELFWEIGHT, "--gamma", "25 kN/m3", "--profile", "kN-m"], capsys)
    assert code == cli.EXIT_OK
    assert "gamma = 25.000 kN/m³" in out
    assert "q = gamma·A = 2.000 kN/m" in out


@pytest.mark.parametrize(
    "extra",
    [
        ["--gamma", "25 kN/m3", "--rho", "1000 kg/m3"],
        ["--gamma", "25 kN/m3", "--g", "10 m/s2"],
        [],
    ],
)
def test_cli_gamma_usage_errors(extra: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main([*SELFWEIGHT, *extra])
    assert info.value.code == cli.EXIT_USAGE
    assert capsys.readouterr().out == ""


def test_cli_gamma_wrong_dimension(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run([*SELFWEIGHT, "--gamma", "1000 kg/m3"], capsys)
    assert (code, out) == (cli.EXIT_DIAGNOSTIC, "")
    assert err.startswith("UNITS_INCOMPATIBLE:")


def test_cli_rho_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run([*SELFWEIGHT, "--rho", "1000 kg/m3", "--g", "10 m/s2"], capsys)
    assert code == cli.EXIT_OK
    assert "q = rho·A·g = 800.000 N/m  ->  qz = -800.000 N/m" in out
