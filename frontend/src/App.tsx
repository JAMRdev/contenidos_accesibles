import { useState, useEffect } from 'react';
import { FileUploader } from './components/FileUploader';
import { AnalysisResults } from './components/AnalysisResults';
import { AppLayout } from './components/layout/AppLayout';
import type { NavSectionId } from './components/layout/Sidebar';
import type { AnalysisStage } from './components/layout/Topbar';
import { analyzeText, analyzePdf } from './services/api';
import type { AnalysisResponse } from './types/analysis';
import {
  Sparkles,
  Folder,
  Settings,
  Home,
  ArrowRight,
  RotateCcw,
} from 'lucide-react';

const VALID_SECTIONS: NavSectionId[] = [
  'inicio',
  'analisis',
  'transformar',
  'materiales',
  'configuracion',
];

function getSectionFromHash(): NavSectionId {
  const raw = window.location.hash.replace(/^#\/?/, '').split('/')[0];
  if (VALID_SECTIONS.includes(raw as NavSectionId)) {
    return raw as NavSectionId;
  }
  return 'analisis';
}

export function App() {
  const [activeSection, setActiveSection] = useState<NavSectionId>(getSectionFromHash);
  const [currentStage, setCurrentStage] = useState<AnalysisStage>('upload');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<AnalysisResponse | null>(null);

  useEffect(() => {
    function handleHashChange() {
      setActiveSection(getSectionFromHash());
    }
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  function handleSelectSection(section: NavSectionId) {
    setActiveSection(section);
    window.location.hash = `#/${section}`;
  }

  async function handleAnalyzePdf(file: File, useMock: boolean) {
    setLoading(true);
    setError(null);
    try {
      const data = await analyzePdf(file, useMock);
      setResults(data);
      setCurrentStage('results');
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Error al procesar el archivo PDF.'
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleAnalyzeText(text: string, useMock: boolean) {
    setLoading(true);
    setError(null);
    try {
      const data = await analyzeText(text, useMock);
      setResults(data);
      setCurrentStage('results');
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Error al procesar el texto.'
      );
    } finally {
      setLoading(false);
    }
  }

  function handleResetAnalysis() {
    setResults(null);
    setError(null);
    setCurrentStage('upload');
  }

  function renderAnalysisSection() {
    return (
      <>
        {error && (
          <div className="via-alert via-alert-danger" role="alert">
            <span>🚨 {error}</span>
            <button
              type="button"
              className="via-alert-close"
              onClick={() => setError(null)}
              aria-label="Cerrar mensaje de error"
            >
              ✕
            </button>
          </div>
        )}

        {currentStage === 'upload' || !results ? (
          <FileUploader
            onAnalyzePdf={handleAnalyzePdf}
            onAnalyzeText={handleAnalyzeText}
            isLoading={loading}
          />
        ) : (
          <div className="via-results-wrapper">
            <div className="via-results-toolbar">
              <button
                type="button"
                className="via-btn via-btn-outline"
                onClick={handleResetAnalysis}
              >
                <RotateCcw size={16} />
                <span>Nuevo análisis</span>
              </button>
            </div>
            <AnalysisResults data={results} />
          </div>
        )}
      </>
    );
  }

  function renderSectionContent() {
    if (activeSection === 'analisis') {
      return renderAnalysisSection();
    }

    if (activeSection === 'inicio') {
      return (
        <div className="via-placeholder-card">
          <div className="via-placeholder-icon">
            <Home size={28} />
          </div>
          <h1 className="via-page-title">Te damos la bienvenida a VIA</h1>
          <p className="via-page-subtitle">
            Tu asistente de accesibilidad docente para diagnosticar, adaptar y
            transformar materiales educativos.
          </p>
          <div className="via-placeholder-actions">
            <button
              type="button"
              className="via-btn via-btn-primary"
              onClick={() => handleSelectSection('analisis')}
            >
              <span>Ir a Análisis integral</span>
              <ArrowRight size={16} />
            </button>
          </div>
        </div>
      );
    }

    if (activeSection === 'transformar') {
      return (
        <div className="via-placeholder-card">
          <div className="via-placeholder-icon">
            <Sparkles size={28} />
          </div>
          <h1 className="via-page-title">Transformar material</h1>
          <p className="via-page-subtitle">
            Convertí un contenido en distintos formatos pedagógicos accesibles
            (resumen en lectura fácil, glosario o secuencia de diapositivas).
          </p>
        </div>
      );
    }

    if (activeSection === 'materiales') {
      return (
        <div className="via-placeholder-card">
          <div className="via-placeholder-icon">
            <Folder size={28} />
          </div>
          <h1 className="via-page-title">Mis materiales</h1>
          <p className="via-page-subtitle">
            Consultá tus documentos analizados, descargá versiones adaptadas y
            gestioná tus enlaces compartidos.
          </p>
        </div>
      );
    }

    return (
      <div className="via-placeholder-card">
        <div className="via-placeholder-icon">
          <Settings size={28} />
        </div>
        <h1 className="via-page-title">Configuración</h1>
        <p className="via-page-subtitle">
          Preferencias de cuenta docente, privacidad y opciones de visualización.
        </p>
      </div>
    );
  }

  return (
    <AppLayout
      activeSection={activeSection}
      onSelectSection={handleSelectSection}
      currentStage={currentStage}
      onSelectStage={(stage) => setCurrentStage(stage)}
      hasResults={Boolean(results)}
      hasAdaptedVersion={false}
    >
      {renderSectionContent()}
    </AppLayout>
  );
}

export default App;
