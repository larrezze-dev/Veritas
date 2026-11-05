"""Codificación y decodificación estricta del modelo a JSON, esquema v1 (plan T-012, D1 y D5).

``encode_*`` produce estructuras JSON (dict, list, str, int, float, bool, None) con TODOS los
datos de cada entidad, incluidas etiquetas y revisiones; ``decode_*`` exige el conjunto exacto
de campos (sin desconocidos ni ausentes), los tipos y los rangos, y construye las entidades con
sus constructores validados del dominio. Nunca se ejecuta nada del archivo: es solo datos.

Cualquier fallo es un ``DiagnosticError`` con clave ``project.*``; no se devuelve un resultado
parcial. Los floats se guardan con su ``repr`` (exacto al volver a leerlos), también −0,0.
"""

import math
from collections.abc import Callable, Sequence
from enum import Enum
from functools import partial
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.connections.end import DofState, EndConnection
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.local_axes import Orientation, ReferenceSource
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCase, LoadCategory
from veritas.core.loads.loads import (
    Load,
    LoadBasis,
    MemberDistributedLoad,
    MemberDistributedMoment,
    MemberPointLoad,
    MemberUniformLoad,
    NodalLoad,
    SelfWeight,
    SelfWeightMode,
    SupportSettlement,
)
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.presentation import Precision, PrecisionMode, PresentationProfile, QuantityKind
from veritas.core.references import RevisionRef
from veritas.core.sections.catalog import (
    SNAPSHOT_PROPERTY_DIMENSIONS,
    CatalogSnapshot,
    SnapshotDimension,
    SnapshotProvenance,
)
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.sections.section import (
    RectangularParameters,
    SectionFamily,
    SectionRevision,
)
from veritas.core.supports.support import (
    BasisKind,
    DofRestraint,
    RestraintKind,
    Support,
    SupportBasis,
)
from veritas.persistence.errors import format_error, value_error

type Json = dict[str, object]

_SECTION_PROPERTY_NAMES: Final = (
    "area",
    "centroid_y",
    "centroid_z",
    "iy",
    "iz",
    "iyz",
    "torsion_constant",
)
_VEC_LEN: Final = 3
_DOFS: Final = 6


# --- Lectura estricta --------------------------------------------------------------------


class Reader:
    """Objeto JSON con el conjunto exacto de campos esperado."""

    def __init__(self, data: object, path: str, fields: Sequence[str]) -> None:
        if not isinstance(data, dict):
            raise format_error("project.invalid_field", {"expected": "objeto"}, path=path)
        unknown = sorted(set(data) - set(fields))
        if unknown:
            raise format_error("project.unknown_fields", {"fields": ", ".join(unknown)}, path=path)
        missing = [name for name in fields if name not in data]
        if missing:
            raise format_error("project.missing_fields", {"fields": ", ".join(missing)}, path=path)
        self._data: Json = data
        self.path = path

    def at(self, name: str) -> str:
        return f"{self.path}.{name}" if self.path else name

    def raw(self, name: str) -> object:
        return self._data[name]

    def _bad(self, name: str, expected: str) -> DiagnosticError:
        return format_error("project.invalid_field", {"expected": expected}, path=self.at(name))

    def obj(self, name: str, fields: Sequence[str]) -> "Reader":
        return Reader(self._data[name], self.at(name), fields)

    def items(self, name: str) -> list[object]:
        value = self._data[name]
        if not isinstance(value, list):
            raise self._bad(name, "lista")
        return value

    def text(self, name: str) -> str:
        value = self._data[name]
        if not isinstance(value, str):
            raise self._bad(name, "texto")
        return value

    def opt_text(self, name: str) -> str | None:
        return None if self._data[name] is None else self.text(name)

    def integer(self, name: str) -> int:
        value = self._data[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._bad(name, "entero")
        return value

    def real(self, name: str) -> float:
        return _real(self._data[name], self.at(name))

    def opt_real(self, name: str) -> float | None:
        return None if self._data[name] is None else self.real(name)

    def uuid(self, name: str) -> UUID:
        return _uuid(self._data[name], self.at(name))

    def enum[E: Enum](self, name: str, kind: type[E]) -> E:
        value = self._data[name]
        try:
            if isinstance(value, str):
                return kind(value)
        except ValueError:
            pass
        allowed = ", ".join(str(item.value) for item in kind)
        raise self._bad(name, f"uno de: {allowed}")

    def vec(self, name: str) -> Vec3:
        return _vec(self._data[name], self.at(name))

    def reals(self, name: str, count: int) -> tuple[float | None, ...]:
        values = self.items(name)
        if len(values) != count:
            raise self._bad(name, f"lista de {count} valores")
        path = self.at(name)
        return tuple(None if v is None else _real(v, path) for v in values)


def _real(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise format_error("project.invalid_field", {"expected": "número"}, path=path)
    try:
        number = float(value)
    except OverflowError:
        raise format_error("project.nonfinite", path=path) from None
    if not math.isfinite(number):
        raise value_error(DiagnosticCode.INPUT_NONFINITE, "project.nonfinite", {"path": path})
    return number


def _uuid(value: object, path: str) -> UUID:
    if isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError:
            parsed = None
        if parsed is not None and str(parsed) == value:
            return parsed
    raise format_error("project.invalid_field", {"expected": "UUID en minúsculas"}, path=path)


def _vec(value: object, path: str) -> Vec3:
    if not isinstance(value, list) or len(value) != _VEC_LEN:
        raise format_error("project.invalid_field", {"expected": "lista de 3 números"}, path=path)
    x, y, z = (_real(item, path) for item in value)
    return Vec3(x, y, z)


def _build[T](path: str, factory: Callable[[], T]) -> T:
    """Construye una entidad; un rechazo del constructor sin diagnóstico se vuelve uno."""
    try:
        return factory()
    except DiagnosticError:
        raise
    except (TypeError, ValueError) as error:
        raise format_error(
            "project.invalid_value", {"reason": str(error)[:200]}, path=path
        ) from None


def _each[T](reader: Reader, name: str, decode: Callable[[object, str], T]) -> tuple[T, ...]:
    return tuple(
        decode(item, f"{reader.at(name)}[{i}]") for i, item in enumerate(reader.items(name))
    )


# --- Referencias y extremos --------------------------------------------------------------

_REF: Final = ("id", "revision")
_END: Final = ("states", "stiffness")


def _enc_ref(ref: RevisionRef) -> Json:
    return {"id": str(ref.entity_id), "revision": ref.revision}


def _dec_ref(data: object, path: str) -> RevisionRef:
    r = Reader(data, path, _REF)
    return _build(path, lambda: RevisionRef(r.uuid("id"), r.integer("revision")))


def _enc_end(end: EndConnection) -> Json:
    return {"states": [str(s) for s in end.states], "stiffness": list(end.stiffness)}


def _dec_end(data: object, path: str) -> EndConnection:
    r = Reader(data, path, _END)
    states = r.items("states")
    if len(states) != _DOFS:
        raise format_error("project.invalid_field", {"expected": "6 estados"}, path=r.at("states"))
    parsed = []
    for i, state in enumerate(states):
        try:
            parsed.append(DofState(str(state)))
        except ValueError:
            raise format_error(
                "project.invalid_field",
                {"expected": "RIGID, RELEASED o SPRING"},
                path=f"{r.at('states')}[{i}]",
            ) from None
    stiffness = r.reals("stiffness", _DOFS)
    return _build(path, lambda: EndConnection(tuple(parsed), stiffness))  # type: ignore[arg-type]


# --- Nudos y barras ----------------------------------------------------------------------

_NODE: Final = ("id", "revision", "label", "position")
_MEMBER: Final = (
    "id",
    "revision",
    "label",
    "node_i",
    "node_j",
    "orientation",
    "material",
    "section",
    "end_i",
    "end_j",
)
_ORIENTATION: Final = ("reference", "source", "alpha")


def _enc_node(node: Node) -> Json:
    return {
        "id": str(node.id),
        "revision": node.revision,
        "label": node.label,
        "position": list(node.position.components()),
    }


def _dec_node(data: object, path: str) -> Node:
    r = Reader(data, path, _NODE)
    return _build(
        path, lambda: Node(r.uuid("id"), r.integer("revision"), r.text("label"), r.vec("position"))
    )


def _enc_member(member: Member) -> Json:
    return {
        "id": str(member.id),
        "revision": member.revision,
        "label": member.label,
        "node_i": str(member.node_i),
        "node_j": str(member.node_j),
        "orientation": {
            "reference": list(member.orientation.reference.components()),
            "source": str(member.orientation.source),
            "alpha": member.orientation.alpha,
        },
        "material": _enc_ref(member.material),
        "section": _enc_ref(member.section),
        "end_i": _enc_end(member.end_i),
        "end_j": _enc_end(member.end_j),
    }


def _dec_member(data: object, path: str) -> Member:
    r = Reader(data, path, _MEMBER)
    o = r.obj("orientation", _ORIENTATION)
    orientation = _build(
        o.path,
        lambda: Orientation(o.vec("reference"), o.enum("source", ReferenceSource), o.real("alpha")),
    )
    return _build(
        path,
        lambda: Member(
            r.uuid("id"),
            r.integer("revision"),
            r.text("label"),
            r.uuid("node_i"),
            r.uuid("node_j"),
            orientation,
            _dec_ref(r.raw("material"), r.at("material")),
            _dec_ref(r.raw("section"), r.at("section")),
            _dec_end(r.raw("end_i"), r.at("end_i")),
            _dec_end(r.raw("end_j"), r.at("end_j")),
        ),
    )


# --- Material y sección ------------------------------------------------------------------

_MATERIAL: Final = (
    "id",
    "revision",
    "name",
    "family",
    "source",
    "standard_edition",
    "elastic_modulus",
    "shear_modulus",
    "density",
    "poisson_ratio",
    "specific_weight",
)
_SECTION: Final = ("id", "revision", "name", "family", "parameters", "properties")
_PROPERTY: Final = ("value", "unit", "origin", "method", "status")


def _enc_material(m: MaterialRevision) -> Json:
    return {
        "id": str(m.id),
        "revision": m.revision,
        "name": m.name,
        "family": str(m.family),
        "source": m.source,
        "standard_edition": m.standard_edition,
        "elastic_modulus": m.elastic_modulus,
        "shear_modulus": m.shear_modulus,
        "density": m.density,
        "poisson_ratio": m.poisson_ratio,
        "specific_weight": m.specific_weight,
    }


def _dec_material(data: object, path: str) -> MaterialRevision:
    r = Reader(data, path, _MATERIAL)
    return _build(
        path,
        lambda: MaterialRevision(
            r.uuid("id"),
            r.integer("revision"),
            r.text("name"),
            r.enum("family", MaterialFamily),
            r.text("source"),
            r.opt_text("standard_edition"),
            r.real("elastic_modulus"),
            r.real("shear_modulus"),
            r.real("density"),
            r.opt_real("poisson_ratio"),
            r.opt_real("specific_weight"),
        ),
    )


def _enc_property(prop: SectionProperty) -> Json:
    return {
        "value": prop.value,
        "unit": prop.unit,
        "origin": prop.origin,
        "method": prop.method,
        "status": str(prop.status),
    }


_CATALOG_PARAMETERS: Final = (
    "catalog",
    "profile",
    "dimensions",
    "provenance",
    *SNAPSHOT_PROPERTY_DIMENSIONS,
    "warnings",
)
_CATALOG_REF: Final = ("id", "name", "source", "license", "edition")
_PROFILE_REF: Final = ("id", "revision", "series", "designation", "shape", "source_sha256")
_SNAPSHOT_DIMENSION: Final = ("name", "value")
_PROVENANCE: Final = ("source", "license", "edition", "method")


def _enc_snapshot(s: CatalogSnapshot) -> Json:
    return {
        "catalog": {
            "id": str(s.catalog_id),
            "name": s.catalog_name,
            "source": s.catalog_source,
            "license": s.catalog_license,
            "edition": s.catalog_edition,
        },
        "profile": {
            "id": str(s.profile_id),
            "revision": s.profile_revision,
            "series": s.series,
            "designation": s.designation,
            "shape": s.shape,
            "source_sha256": s.source_sha256,
        },
        "dimensions": [{"name": d.name, "value": d.value} for d in s.dimensions],
        "provenance": {
            "source": s.provenance.source,
            "license": s.provenance.license,
            "edition": s.provenance.edition,
            "method": s.provenance.method,
        },
        **{name: _enc_property(getattr(s, name)) for name in SNAPSHOT_PROPERTY_DIMENSIONS},
        "warnings": list(s.warnings),
    }


def _enc_section(s: SectionRevision) -> Json:
    properties: Json = {
        name: _enc_property(getattr(s.properties, name)) for name in _SECTION_PROPERTY_NAMES
    }
    parameters: Json
    if isinstance(s.parameters, CatalogSnapshot):
        parameters = _enc_snapshot(s.parameters)
    else:
        parameters = {"b": s.parameters.b, "h": s.parameters.h}
    return {
        "id": str(s.id),
        "revision": s.revision,
        "name": s.name,
        "family": str(s.family),
        "parameters": parameters,
        "properties": properties,
    }


def _dec_property(data: object, path: str) -> SectionProperty:
    r = Reader(data, path, _PROPERTY)
    return _build(
        path,
        lambda: SectionProperty(
            r.opt_real("value"),
            r.text("unit"),
            r.text("origin"),
            r.text("method"),
            r.enum("status", PropertyStatus),
        ),
    )


def _text_item(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise format_error("project.invalid_field", {"expected": "texto"}, path=path)
    return value


def _dec_snapshot_dimension(data: object, path: str) -> SnapshotDimension:
    r = Reader(data, path, _SNAPSHOT_DIMENSION)
    return _build(path, lambda: SnapshotDimension(r.text("name"), r.real("value")))


def _dec_snapshot(data: object, path: str) -> CatalogSnapshot:
    r = Reader(data, path, _CATALOG_PARAMETERS)
    catalog = r.obj("catalog", _CATALOG_REF)
    profile = r.obj("profile", _PROFILE_REF)
    prov = r.obj("provenance", _PROVENANCE)
    dimensions = tuple(
        _dec_snapshot_dimension(item, f"{r.at('dimensions')}[{i}]")
        for i, item in enumerate(r.items("dimensions"))
    )
    props = {name: _dec_property(r.raw(name), r.at(name)) for name in SNAPSHOT_PROPERTY_DIMENSIONS}
    warnings = tuple(_text_item(item, r.at("warnings")) for item in r.items("warnings"))
    return _build(
        path,
        lambda: CatalogSnapshot(
            catalog_id=catalog.uuid("id"),
            catalog_name=catalog.text("name"),
            catalog_source=catalog.text("source"),
            catalog_license=catalog.text("license"),
            catalog_edition=catalog.text("edition"),
            profile_id=profile.uuid("id"),
            profile_revision=profile.integer("revision"),
            series=profile.text("series"),
            designation=profile.text("designation"),
            shape=profile.text("shape"),
            source_sha256=profile.text("source_sha256"),
            dimensions=dimensions,
            provenance=SnapshotProvenance(
                prov.text("source"), prov.text("license"), prov.text("edition"), prov.text("method")
            ),
            warnings=warnings,
            **props,
        ),
    )


def _dec_parameters(family: SectionFamily, r: Reader) -> RectangularParameters | CatalogSnapshot:
    """Decodifica ``parameters`` según el discriminador ``family`` (esquema v4, ADR-010)."""
    if family is SectionFamily.CATALOGO:
        return _dec_snapshot(r.raw("parameters"), r.at("parameters"))
    p = r.obj("parameters", ("b", "h"))
    return _build(p.path, lambda: RectangularParameters(p.real("b"), p.real("h")))


def _dec_section(data: object, path: str) -> SectionRevision:
    r = Reader(data, path, _SECTION)
    family = r.enum("family", SectionFamily)
    parameters = _dec_parameters(family, r)
    pr = r.obj("properties", _SECTION_PROPERTY_NAMES)
    properties = _build(
        pr.path,
        lambda: SectionProperties(
            *(_dec_property(pr.raw(name), pr.at(name)) for name in _SECTION_PROPERTY_NAMES)
        ),
    )
    return _build(
        path,
        lambda: SectionRevision(
            r.uuid("id"),
            r.integer("revision"),
            r.text("name"),
            family,
            parameters,
            properties,
        ),
    )


# --- Apoyos ------------------------------------------------------------------------------

_SUPPORT: Final = ("id", "revision", "label", "node_id", "basis", "restraints")
_BASIS: Final = ("kind", "rows")
_RESTRAINT: Final = ("kind", "stiffness")


def _enc_support(s: Support) -> Json:
    rows = None if s.basis.rows is None else [list(row.components()) for row in s.basis.rows]
    return {
        "id": str(s.id),
        "revision": s.revision,
        "label": s.label,
        "node_id": str(s.node_id),
        "basis": {"kind": str(s.basis.kind), "rows": rows},
        "restraints": [{"kind": str(r.kind), "stiffness": r.stiffness} for r in s.restraints],
    }


def _dec_basis(data: object, path: str) -> SupportBasis:
    r = Reader(data, path, _BASIS)
    kind = r.enum("kind", BasisKind)
    raw_rows = r.raw("rows")
    rows: tuple[Vec3, Vec3, Vec3] | None = None
    if raw_rows is not None:
        items = r.items("rows")
        if len(items) != _VEC_LEN:
            raise format_error("project.invalid_field", {"expected": "3 filas"}, path=r.at("rows"))
        a, b, c = (_vec(item, f"{r.at('rows')}[{i}]") for i, item in enumerate(items))
        rows = (a, b, c)
    return _build(path, lambda: SupportBasis(kind, rows))


def _restraint(s: Reader) -> DofRestraint:
    return DofRestraint(s.enum("kind", RestraintKind), s.opt_real("stiffness"))


def _dec_support(data: object, path: str) -> Support:
    r = Reader(data, path, _SUPPORT)
    states = r.items("restraints")
    if len(states) != _DOFS:
        raise format_error(
            "project.invalid_field", {"expected": "6 estados"}, path=r.at("restraints")
        )
    restraints = []
    for i, item in enumerate(states):
        item_path = f"{r.at('restraints')}[{i}]"
        s = Reader(item, item_path, _RESTRAINT)
        restraints.append(
            _build(
                item_path,
                partial(_restraint, s),
            )
        )
    basis = _dec_basis(r.raw("basis"), r.at("basis"))
    return _build(
        path,
        lambda: Support(
            r.uuid("id"),
            r.integer("revision"),
            r.text("label"),
            r.uuid("node_id"),
            basis,
            tuple(restraints),  # type: ignore[arg-type]
        ),
    )


# --- Casos y cargas ----------------------------------------------------------------------

_CASE: Final = ("id", "revision", "name", "category", "description")
_LOAD_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "nodal": ("id", "revision", "case_id", "node_id", "force", "moment"),
    "member_uniform": ("id", "revision", "case_id", "member_id", "basis", "intensity"),
    "self_weight": ("id", "revision", "case_id", "gravity", "mode", "excluded_members"),
    "settlement": ("id", "revision", "case_id", "support_id", "translation", "rotation"),
    "member_point": (
        "id",
        "revision",
        "case_id",
        "member_id",
        "basis",
        "position",
        "force",
        "moment",
    ),
    "member_distributed": (
        "id",
        "revision",
        "case_id",
        "member_id",
        "basis",
        "start",
        "end",
        "intensity_start",
        "intensity_end",
    ),
    "member_moment": (
        "id",
        "revision",
        "case_id",
        "member_id",
        "basis",
        "start",
        "end",
        "intensity_start",
        "intensity_end",
    ),
}


def _enc_case(c: LoadCase) -> Json:
    return {
        "id": str(c.id),
        "revision": c.revision,
        "name": c.name,
        "category": str(c.category),
        "description": c.description,
    }


def _dec_case(data: object, path: str) -> LoadCase:
    r = Reader(data, path, _CASE)
    return _build(
        path,
        lambda: LoadCase(
            r.uuid("id"),
            r.integer("revision"),
            r.text("name"),
            r.enum("category", LoadCategory),
            r.text("description"),
        ),
    )


def _vec_json(v: Vec3) -> list[float]:
    return list(v.components())


def _enc_load(load: Load) -> Json:
    base: Json = {"id": str(load.id), "revision": load.revision, "case_id": str(load.case_id)}
    match load:
        case NodalLoad():
            return {
                "type": "nodal",
                **base,
                "node_id": str(load.node_id),
                "force": _vec_json(load.force),
                "moment": _vec_json(load.moment),
            }
        case MemberUniformLoad():
            return {
                "type": "member_uniform",
                **base,
                "member_id": str(load.member_id),
                "basis": str(load.basis),
                "intensity": _vec_json(load.intensity),
            }
        case SelfWeight():
            return {
                "type": "self_weight",
                **base,
                "gravity": load.gravity,
                "mode": str(load.mode),
                "excluded_members": sorted(str(m) for m in load.excluded_members),
            }
        case SupportSettlement():
            return {
                "type": "settlement",
                **base,
                "support_id": str(load.support_id),
                "translation": _vec_json(load.translation),
                "rotation": _vec_json(load.rotation),
            }
        case MemberPointLoad():
            return {
                "type": "member_point",
                **base,
                "member_id": str(load.member_id),
                "basis": str(load.basis),
                "position": load.position,
                "force": _vec_json(load.force),
                "moment": _vec_json(load.moment),
            }
        case MemberDistributedLoad() | MemberDistributedMoment():
            kind = (
                "member_distributed" if isinstance(load, MemberDistributedLoad) else "member_moment"
            )
            return {
                "type": kind,
                **base,
                "member_id": str(load.member_id),
                "basis": str(load.basis),
                "start": load.start,
                "end": load.end,
                "intensity_start": _vec_json(load.intensity_start),
                "intensity_end": _vec_json(load.intensity_end),
            }
    raise TypeError(f"Tipo de carga no persistible: {type(load).__name__}")  # pragma: no cover


def _dec_load(data: object, path: str) -> Load:
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise format_error("project.invalid_field", {"expected": "objeto con «type»"}, path=path)
    kind = data["type"]
    if kind not in _LOAD_FIELDS:
        raise format_error(
            "project.invalid_field",
            {"expected": "tipo de carga conocido: " + ", ".join(_LOAD_FIELDS)},
            path=f"{path}.type",
        )
    r = Reader({k: v for k, v in data.items() if k != "type"}, path, _LOAD_FIELDS[kind])
    ident = (r.uuid("id"), r.integer("revision"), r.uuid("case_id"))
    if kind == "nodal":
        return _build(
            path, lambda: NodalLoad(*ident, r.uuid("node_id"), r.vec("force"), r.vec("moment"))
        )
    if kind == "member_uniform":
        return _build(
            path,
            lambda: MemberUniformLoad(
                *ident, r.uuid("member_id"), r.enum("basis", LoadBasis), r.vec("intensity")
            ),
        )
    if kind == "self_weight":
        excluded = frozenset(
            _uuid(item, f"{r.at('excluded_members')}[{i}]")
            for i, item in enumerate(r.items("excluded_members"))
        )
        return _build(
            path,
            lambda: SelfWeight(
                *ident, r.opt_real("gravity"), r.enum("mode", SelfWeightMode), excluded
            ),
        )
    if kind == "settlement":
        return _build(
            path,
            lambda: SupportSettlement(
                *ident, r.uuid("support_id"), r.vec("translation"), r.vec("rotation")
            ),
        )
    if kind == "member_point":
        return _build(
            path,
            lambda: MemberPointLoad(
                *ident,
                r.uuid("member_id"),
                r.enum("basis", LoadBasis),
                r.real("position"),
                r.vec("force"),
                r.vec("moment"),
            ),
        )
    cls = MemberDistributedLoad if kind == "member_distributed" else MemberDistributedMoment
    return _build(
        path,
        lambda: cls(
            *ident,
            r.uuid("member_id"),
            r.enum("basis", LoadBasis),
            r.real("start"),
            r.real("end"),
            r.vec("intensity_start"),
            r.vec("intensity_end"),
        ),
    )


# --- Modelo completo ---------------------------------------------------------------------

_MODEL: Final = (
    "nodes",
    "members",
    "materials",
    "sections",
    "supports",
    "load_cases",
    "loads",
    "model_revision",
)


def encode_snapshot(snapshot: ModelSnapshot) -> Json:
    """Estructura JSON completa del modelo (con etiquetas y revisiones)."""
    return {
        "nodes": [_enc_node(n) for n in snapshot.nodes],
        "members": [_enc_member(m) for m in snapshot.members],
        "materials": [_enc_material(m) for m in snapshot.materials],
        "sections": [_enc_section(s) for s in snapshot.sections],
        "supports": [_enc_support(s) for s in snapshot.supports],
        "load_cases": [_enc_case(c) for c in snapshot.load_cases],
        "loads": [_enc_load(load) for load in snapshot.loads],
        "model_revision": snapshot.model_revision,
    }


def decode_snapshot(data: object, path: str = "content" + ".model") -> ModelSnapshot:
    """Valida la estructura y construye el ``ModelSnapshot`` (sin comprobar referencias)."""
    r = Reader(data, path, _MODEL)
    revision = r.integer("model_revision")
    if revision < 0:
        raise format_error(
            "project.invalid_field", {"expected": "entero >= 0"}, path=r.at("model_revision")
        )
    return ModelSnapshot(
        nodes=_each(r, "nodes", _dec_node),
        members=_each(r, "members", _dec_member),
        materials=_each(r, "materials", _dec_material),
        sections=_each(r, "sections", _dec_section),
        supports=_each(r, "supports", _dec_support),
        load_cases=_each(r, "load_cases", _dec_case),
        loads=_each(r, "loads", _dec_load),
        model_revision=revision,
    )


# --- Perfiles de presentación de usuario (D12-A) -----------------------------------------

_PROFILE: Final = ("name", "units", "precision")
_PRECISION: Final = ("mode", "digits")


def encode_profile(profile: PresentationProfile) -> Json:
    """Perfil de usuario: nombre, símbolo de unidad y precisión por magnitud."""
    return {
        "name": profile.name,
        "units": {str(kind): unit.symbol for kind, unit in profile.units},
        "precision": {
            str(kind): {"mode": str(p.mode), "digits": p.digits} for kind, p in profile.precision
        },
    }


def _kind_map(r: Reader, name: str) -> dict[QuantityKind, object]:
    raw = r.raw(name)
    if not isinstance(raw, dict):
        raise format_error("project.invalid_field", {"expected": "objeto"}, path=r.at(name))
    result: dict[QuantityKind, object] = {}
    for key, value in raw.items():
        try:
            result[QuantityKind(key)] = value
        except ValueError:
            raise format_error(
                "project.invalid_field",
                {"expected": "magnitud conocida"},
                path=f"{r.at(name)}.{key}",
            ) from None
    return result


def _precision(p: Reader) -> Precision:
    return Precision(p.enum("mode", PrecisionMode), p.integer("digits"))


def decode_profile(data: object, path: str) -> PresentationProfile:
    """Perfil de usuario; los nombres de perfiles integrados se rechazan."""
    r = Reader(data, path, _PROFILE)
    units: list[tuple[QuantityKind, u.Unit]] = []
    for kind, symbol in _kind_map(r, "units").items():
        if not isinstance(symbol, str):
            raise format_error(
                "project.invalid_field",
                {"expected": "símbolo de unidad"},
                path=f"{r.at('units')}.{kind}",
            )
        units.append((kind, _build(r.at("units"), partial(u.get_unit, symbol))))
    precision: list[tuple[QuantityKind, Precision]] = []
    for kind, entry in _kind_map(r, "precision").items():
        p = Reader(entry, f"{r.at('precision')}.{kind}", _PRECISION)
        precision.append(
            (
                kind,
                _build(
                    p.path,
                    partial(_precision, p),
                ),
            )
        )
    return _build(
        path, lambda: PresentationProfile(r.text("name"), tuple(units), precision=tuple(precision))
    )
