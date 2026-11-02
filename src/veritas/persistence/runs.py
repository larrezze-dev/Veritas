"""Ejecuciones históricas inmutables y vigencia CURRENT/STALE (plan T-012, D8-A y D9-A; DAT-004).

Un ``RunRecord`` guarda una corrida (plana o espacial) con su procedencia completa, su resultado
normalizado y el hash canónico del modelo con el que se calculó. Los registros solo se añaden;
nunca se modifican. La vigencia NO se guarda: se calcula comparando el hash guardado con el del
modelo actual (``validity``). Una corrida FAILED nunca lleva resultados ni se presenta como
válida; una corrida SUCCEEDED cuya normalización falló se guarda sin resultados normalizados
(``usable`` es False).
"""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.model import StructuralModel
from veritas.fem.results.normalized import PlanarResults
from veritas.fem.results.run import AnalysisRun, RunStatus
from veritas.fem.spatial.results import SpatialAnalysisRun, SpatialResults
from veritas.persistence.canonical import CANON_VERSION, model_hash
from veritas.persistence.codec import decode, encode
from veritas.persistence.errors import format_error, value_error
from veritas.persistence.schema import Json, Reader

_HASH: Final = re.compile(r"[0-9a-f]{64}")
_RECORD: Final = ("kind", "model_hash", "canon_version", "run", "results")


class RunKind(StrEnum):
    """Adaptador que produjo la corrida."""

    PLANAR = "planar"
    SPATIAL = "spatial"


class Validity(StrEnum):
    """Vigencia de una corrida respecto del modelo actual (ADR-001 §4)."""

    CURRENT = "CURRENT"
    STALE = "STALE"


type Run = AnalysisRun | SpatialAnalysisRun
type Results = PlanarResults | SpatialResults

_CLASSES: Final = {
    RunKind.PLANAR: (AnalysisRun, PlanarResults),
    RunKind.SPATIAL: (SpatialAnalysisRun, SpatialResults),
}


def _invalid(reason: str) -> InputValueError:
    return value_error(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "project.invalid_value",
        {"path": "run", "reason": reason},
    )


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Corrida guardada: procedencia, resultado normalizado y hash del modelo."""

    kind: RunKind
    run: Run
    results: Results | None
    model_hash: str
    canon_version: str = CANON_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RunKind):
            raise TypeError("RunRecord.kind debe ser RunKind")
        run_class, results_class = _CLASSES[self.kind]
        if not isinstance(self.run, run_class):
            raise _invalid(f"la corrida no es del tipo {self.kind}")
        if not isinstance(self.model_hash, str) or not _HASH.fullmatch(self.model_hash):
            raise _invalid("hash del modelo no válido (SHA-256 hexadecimal en minúsculas)")
        if self.run.model_hash != self.model_hash:
            raise _invalid("el hash del modelo de la corrida no coincide con el del registro")
        if self.results is not None:
            if self.run.status is not RunStatus.SUCCEEDED:
                raise _invalid("solo una corrida SUCCEEDED puede llevar resultados")
            if not isinstance(self.results, results_class):
                raise _invalid(f"los resultados no son del tipo {self.kind}")
            if self.results.run_id != self.run.run_id:
                raise _invalid("los resultados pertenecen a otra corrida")

    @property
    def run_id(self) -> UUID:
        return self.run.run_id

    @property
    def usable(self) -> bool:
        """True solo si la corrida terminó SUCCEEDED y tiene resultados normalizados."""
        return self.run.status is RunStatus.SUCCEEDED and self.results is not None


def make_record(model: StructuralModel, run: Run, results: Results | None = None) -> RunRecord:
    """Registro de una corrida con el hash del modelo con el que se calculó.

    El modelo no debe haber cambiado desde la corrida (misma ``model_revision``): si no, el
    hash no correspondería a lo analizado y se rechaza.
    """
    kind = RunKind.SPATIAL if isinstance(run, SpatialAnalysisRun) else RunKind.PLANAR
    if run.model_revision != model.model_revision:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "project.run_model_changed",
            {"run_revision": run.model_revision, "model_revision": model.model_revision},
        )
    digest = model_hash(model.snapshot())
    return RunRecord(kind, replace(run, model_hash=digest), results, digest)


def validity(record: RunRecord, model: StructuralModel) -> Validity:
    """CURRENT si el hash guardado coincide con el del modelo actual; si no, STALE."""
    if record.canon_version != CANON_VERSION:
        return Validity.STALE
    same = record.model_hash == model_hash(model.snapshot())
    return Validity.CURRENT if same else Validity.STALE


def encode_record(record: RunRecord) -> Json:
    """Estructura JSON de un registro."""
    return {
        "kind": str(record.kind),
        "model_hash": record.model_hash,
        "canon_version": record.canon_version,
        "run": encode(record.run, "run"),
        "results": None if record.results is None else encode(record.results, "results"),
    }


def decode_record(data: object, path: str) -> RunRecord:
    """Registro validado de forma estricta; ``DiagnosticError`` si algo no encaja."""
    reader = Reader(data, path, _RECORD)
    kind = reader.enum("kind", RunKind)
    run_class, results_class = _CLASSES[kind]
    run = decode(run_class, reader.raw("run"), reader.at("run"))
    raw_results = reader.raw("results")
    results = (
        None if raw_results is None else decode(results_class, raw_results, reader.at("results"))
    )
    try:
        return RunRecord(
            kind, run, results, reader.text("model_hash"), reader.text("canon_version")
        )
    except (TypeError, ValueError) as error:
        if hasattr(error, "diagnostic"):
            raise
        raise format_error(
            "project.invalid_value", {"reason": str(error)[:200]}, path=path
        ) from None
