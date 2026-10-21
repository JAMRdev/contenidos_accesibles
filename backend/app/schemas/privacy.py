"""
Esquemas de respuesta para la política de privacidad y tratamiento de datos (Issue #41).
"""

from pydantic import BaseModel, Field


class ExternalAIProcessingPolicy(BaseModel):
    """Información sobre el procesamiento con proveedores externos de IA."""

    enabled: bool = Field(description="Indica si el análisis utiliza un servicio externo de IA.")
    provider: str = Field(description="Proveedor de IA configurado en el backend (ej. groq, gemini).")
    model: str = Field(description="Modelo de IA utilizado para el análisis pedagógico.")
    max_words_per_analysis: int = Field(description="Cantidad máxima de palabras enviadas a la IA por análisis.")
    data_sent_description: str = Field(description="Descripción de qué datos se transmiten al proveedor de IA.")
    pii_protection: str = Field(description="Mecanismo de detección y bloqueo preventivo de datos personales.")


class StoragePolicy(BaseModel):
    """Información sobre el almacenamiento de archivos y retención."""

    local_disk_storage: str = Field(description="Garantía de no persistencia en el disco local del servidor.")
    storage_backend: str = Field(description="Backend de almacenamiento activo ('r2' o 'local').")
    storage_provider_name: str = Field(description="Nombre descriptivo del proveedor de almacenamiento.")
    retention_period_hours: int = Field(description="Horas de retención antes de la expiración y purga automática.")
    auto_deletion_enabled: bool = Field(description="Indica si la purga automática por vencimiento está habilitada.")
    object_folders: dict[str, str] = Field(description="Carpetas de almacenamiento utilizadas para segregar contenidos.")


class UsageStatisticsPolicy(BaseModel):
    """Historial de uso por usuario para las estadísticas del panel de administración (Issue #104)."""

    enabled: bool = Field(description="Indica si se registra el historial de uso por usuario.")
    retention: str = Field(description="Cuánto se conserva el historial.")
    deleted_with_materials: bool = Field(
        description="Indica si el historial se borra junto con los diagnósticos y archivos al vencer (false: se conserva)."
    )
    recorded_data: list[str] = Field(description="Qué se registra de cada acción.")
    never_recorded: list[str] = Field(description="Qué nunca se registra.")
    visible_to: str = Field(description="Quién puede ver el historial.")


class PrivacyPolicyResponse(BaseModel):
    """Respuesta con la política de privacidad activa para visualización en Frontend."""

    external_ai_processing: ExternalAIProcessingPolicy
    storage_policy: StoragePolicy
    usage_statistics: UsageStatisticsPolicy
    user_rights: str = Field(description="Declaración de derechos y control docente sobre sus materiales.")
