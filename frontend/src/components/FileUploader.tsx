import { useState, useRef } from 'react';
import {
  Sparkles,
  FileUp,
  Upload,
  Camera,
  BookOpen,
  ListTree,
  Eye,
  CheckCircle2,
  FileText,
} from 'lucide-react';

interface FileUploaderProps {
  onAnalyzePdf: (file: File, useMock: boolean) => void;
  onAnalyzeText: (text: string, useMock: boolean) => void;
  isLoading: boolean;
}

export function FileUploader(props: FileUploaderProps) {
  const [tab, setTab] = useState<'pdf' | 'text'>('pdf');
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState<string>('');
  const [useMock, setUseMock] = useState<boolean>(true);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setValidationError(null);
    }
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(true);
  }

  function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setTab('pdf');
      setFile(e.dataTransfer.files[0]);
      setValidationError(null);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setValidationError(null);

    if (tab === 'pdf') {
      if (!file) {
        setValidationError('Por favor, seleccioná un archivo para analizar.');
        return;
      }
      props.onAnalyzePdf(file, useMock);
    } else {
      if (text.trim().length < 10) {
        setValidationError('El texto debe tener al menos 10 caracteres.');
        return;
      }
      props.onAnalyzeText(text, useMock);
    }
  }

  return (
    <div className="via-upload-view">
      <div className="via-page-heading">
        <h1 className="via-page-title">¿Listo para analizar?</h1>
        <p className="via-page-subtitle">
          Adaptá tu contenido educativo para que sea más accesible.
        </p>
      </div>

      <div className="via-welcome-card" role="region" aria-label="Bienvenida">
        <div className="via-welcome-icon" aria-hidden="true">
          <Sparkles size={20} />
        </div>
        <div className="via-welcome-text">
          <h2 className="via-welcome-title">
            Te damos la bienvenida al análisis integral
          </h2>
          <p className="via-welcome-desc">
            Subí tu material. Te guiaremos paso a paso con recomendaciones
            claras para que sea fácil de entender, navegar y usar.
          </p>
        </div>
      </div>

      <div className="via-upload-toolbar">
        <div className="via-mode-tabs" role="tablist" aria-label="Modo de entrada">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'pdf'}
            className={`via-mode-tab ${tab === 'pdf' ? 'active' : ''}`}
            onClick={() => {
              setTab('pdf');
              setValidationError(null);
            }}
          >
            <FileUp size={16} />
            <span>Subir documento</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'text'}
            className={`via-mode-tab ${tab === 'text' ? 'active' : ''}`}
            onClick={() => {
              setTab('text');
              setValidationError(null);
            }}
          >
            <FileText size={16} />
            <span>Pegar texto</span>
          </button>
        </div>

        <label className="via-mock-toggle">
          <input
            type="checkbox"
            checked={useMock}
            onChange={() => setUseMock(!useMock)}
          />
          <span>Modo prueba rápida (Mock)</span>
        </label>
      </div>

      <form onSubmit={handleSubmit}>
        <div
          className={`via-dropzone ${isDragging ? 'dragging' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {tab === 'pdf' ? (
            <>
              <div className="via-dropzone-icon-box" aria-hidden="true">
                <FileUp size={30} strokeWidth={2} />
              </div>

              <h3 className="via-dropzone-title">
                Arrastrá y soltá tu archivo acá
              </h3>
              <p className="via-dropzone-subtitle">
                También podés buscarlo en tu computadora.
              </p>

              <div className="via-dropzone-actions">
                <label className="via-btn via-btn-primary">
                  <Upload size={16} />
                  <span>{file ? file.name : 'Seleccionar archivo'}</span>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.doc,.docx,.ppt,.pptx"
                    onChange={handleFileChange}
                    style={{ display: 'none' }}
                  />
                </label>

                <span className="via-dropzone-or" aria-hidden="true">
                  ó
                </span>

                <button
                  type="button"
                  className="via-btn via-btn-yellow"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Camera size={16} />
                  <span>Sacar foto</span>
                </button>
              </div>

              <p className="via-dropzone-formats">
                Formatos permitidos: PDF, DOC, DOCX, PPT y PPTX
              </p>
            </>
          ) : (
            <div className="via-text-input-container">
              <h3 className="via-dropzone-title">Pegá el texto a analizar</h3>
              <p className="via-dropzone-subtitle">
                Ingresá el fragmento o actividad educativa que querés evaluar.
              </p>
              <textarea
                className="via-textarea"
                rows={6}
                placeholder="Ingresá o pegá el texto educativo acá..."
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
          )}

          {validationError && (
            <p className="via-validation-error" role="alert">
              ⚠️ {validationError}
            </p>
          )}

          {(file || (tab === 'text' && text.trim().length > 0)) && (
            <div className="via-submit-row">
              <button
                type="submit"
                className="via-btn via-btn-primary via-btn-lg"
                disabled={props.isLoading}
              >
                {props.isLoading ? 'Analizando material...' : 'Iniciar análisis'}
              </button>
            </div>
          )}
        </div>
      </form>

      <section className="via-dimensions-section" aria-labelledby="dimensions-heading">
        <h2 id="dimensions-heading" className="via-section-title">
          ¿Qué analiza la herramienta?
        </h2>
        <p className="via-section-subtitle">
          Revisamos cinco dimensiones para detectar barreras y oportunidades de
          mejora.
        </p>

        <div className="via-dimensions-grid">
          <article className="via-dimension-card">
            <div className="via-dimension-icon" aria-hidden="true">
              <BookOpen size={20} />
            </div>
            <h3 className="via-dimension-name">Comprensión</h3>
            <p className="via-dimension-tags">
              Lenguaje · oraciones · consignas
            </p>
            <div className="via-dimension-status">
              <CheckCircle2 size={15} />
              <span>Incluida en el análisis</span>
            </div>
          </article>

          <article className="via-dimension-card">
            <div className="via-dimension-icon" aria-hidden="true">
              <ListTree size={20} />
            </div>
            <h3 className="via-dimension-name">Estructura</h3>
            <p className="via-dimension-tags">Títulos · secciones · orden</p>
            <div className="via-dimension-status">
              <CheckCircle2 size={15} />
              <span>Incluida en el análisis</span>
            </div>
          </article>

          <article className="via-dimension-card">
            <div className="via-dimension-icon" aria-hidden="true">
              <Eye size={20} />
            </div>
            <h3 className="via-dimension-name">Accesibilidad visual</h3>
            <p className="via-dimension-tags">
              Imágenes · contraste · legibilidad
            </p>
            <div className="via-dimension-status">
              <CheckCircle2 size={15} />
              <span>Incluida en el análisis</span>
            </div>
          </article>
        </div>
      </section>
    </div>
  );
}
