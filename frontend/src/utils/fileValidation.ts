export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB
export const MIN_TEXT_CHARS = 10;
export const MAX_TEXT_CHARS = 20_000;

export const SUPPORTED_FORMATS = {
  '.pdf': {
    label: 'PDF',
    mime: 'application/pdf',
  },
  '.docx': {
    label: 'Word (.docx)',
    mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  },
  '.pptx': {
    label: 'PowerPoint (.pptx)',
    mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  },
} as const;

export type SupportedExtension = keyof typeof SUPPORTED_FORMATS;

export const ACCEPT_ATTRIBUTE = [
  '.pdf',
  '.docx',
  '.pptx',
  SUPPORTED_FORMATS['.pdf'].mime,
  SUPPORTED_FORMATS['.docx'].mime,
  SUPPORTED_FORMATS['.pptx'].mime,
].join(',');

const LEGACY_OFFICE_EXTENSIONS = new Set(['.doc', '.ppt']);

const ALLOWED_MIMES_BY_EXTENSION: Record<SupportedExtension, ReadonlySet<string>> = {
  '.pdf': new Set([
    'application/pdf',
    'application/x-pdf',
    'application/octet-stream',
  ]),
  '.docx': new Set([
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/octet-stream',
    'application/zip',
    'application/x-zip-compressed',
  ]),
  '.pptx': new Set([
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/octet-stream',
    'application/zip',
    'application/x-zip-compressed',
  ]),
};

export type FileValidationErrorReason =
  | 'unsupported_format'
  | 'legacy_office'
  | 'invalid_mime'
  | 'too_large'
  | 'empty';

export type FileValidationResult =
  | {
      ok: true;
      extension: SupportedExtension;
      label: string;
      mime: string;
    }
  | {
      ok: false;
      reason: FileValidationErrorReason;
      message: string;
    };

export function getFileExtension(filename: string): string {
  const trimmed = filename.trim();
  const dotIdx = trimmed.lastIndexOf('.');
  if (dotIdx <= 0 || dotIdx === trimmed.length - 1) {
    return '';
  }
  return trimmed.slice(dotIdx).toLowerCase();
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) {
    const kb = Math.max(1, Math.round(bytes / 1024));
    return `${kb} KB`;
  }
  const mb = (bytes / (1024 * 1024)).toFixed(2).replace('.', ',');
  return `${mb} MB`;
}

export function getFileCacheKey(file: Pick<File, 'name' | 'size' | 'lastModified'>): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

export function validateFile(
  file: Pick<File, 'name' | 'size' | 'type'>,
): FileValidationResult {
  const ext = getFileExtension(file.name);

  if (LEGACY_OFFICE_EXTENSIONS.has(ext)) {
    return {
      ok: false,
      reason: 'legacy_office',
      message:
        'Los formatos antiguos (.doc y .ppt) no son compatibles. Guardá el archivo como .docx o .pptx.',
    };
  }

  if (!(ext in SUPPORTED_FORMATS)) {
    return {
      ok: false,
      reason: 'unsupported_format',
      message:
        'Formato no soportado. Seleccioná un archivo PDF (.pdf), Word (.docx) o PowerPoint (.pptx).',
    };
  }

  const supportedExt = ext as SupportedExtension;
  const formatInfo = SUPPORTED_FORMATS[supportedExt];

  if (file.size <= 0) {
    return {
      ok: false,
      reason: 'empty',
      message: 'El archivo seleccionado está vacío (0 bytes).',
    };
  }

  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      ok: false,
      reason: 'too_large',
      message: `El archivo supera el límite máximo permitido de 10 MB (pesa ${formatBytes(file.size)}).`,
    };
  }

  const rawType = (file.type || '').split(';')[0].trim().toLowerCase();
  if (rawType && !ALLOWED_MIMES_BY_EXTENSION[supportedExt].has(rawType)) {
    return {
      ok: false,
      reason: 'invalid_mime',
      message: `El tipo de archivo (${rawType}) no coincide con un documento ${formatInfo.label} válido.`,
    };
  }

  return {
    ok: true,
    extension: supportedExt,
    label: formatInfo.label,
    mime: formatInfo.mime,
  };
}

export function validateText(text: string): string | null {
  const trimmed = text.trim();
  if (trimmed.length < MIN_TEXT_CHARS) {
    return `El texto debe tener al menos ${MIN_TEXT_CHARS} caracteres.`;
  }
  if (text.length > MAX_TEXT_CHARS) {
    return `El texto supera el límite máximo de ${MAX_TEXT_CHARS.toLocaleString('es-AR')} caracteres.`;
  }
  return null;
}
