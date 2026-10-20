from typing import Literal

from pydantic import BaseModel, Field


class CleanupRun(BaseModel):
    ran_at: str = Field(description="Cuándo corrió la limpieza (UTC, ISO 8601).")
    expired: int = Field(description="Archivos vencidos borrados del almacenamiento y de la base.")
    abandoned: int = Field(description="Subidas que nunca se confirmaron, borradas.")
    errors: int = Field(description="Archivos que el almacenamiento no pudo borrar: se reintentan en la próxima limpieza.")


class RetentionInfo(BaseModel):
    hours: int = Field(description="Retención vigente de los archivos nuevos, en horas.")
    default_hours: int = Field(description="Valor por defecto (FILE_RETENTION_HOURS).")
    max_hours: int = Field(description="Máximo permitido (FILE_RETENTION_MAX_HOURS).")
    source: Literal["panel", "env"] = Field(description="'panel' si la cambió un admin; 'env' si vale el valor por defecto.")
    updated_at: str | None = None
    updated_by: str | None = None
    cleanup_interval_minutes: int = Field(description="Cada cuántos minutos corre la limpieza (0 = desactivada).")
    last_cleanup: CleanupRun | None = None


class RetentionUpdate(BaseModel):
    hours: int = Field(ge=1, description="Nueva retención en horas, hasta el máximo permitido.")
