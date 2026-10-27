import { useState, useEffect } from 'react';
import { RotateCcw } from 'lucide-react';
import { FileUploader } from './components/FileUploader';
import { AnalysisResults } from './components/AnalysisResults';
import { AppLayout } from './components/layout/AppLayout';
import type { NavSectionId } from './components/layout/Sidebar';
import type { AnalysisStage } from './components/layout/Topbar';
import { SectionPlaceholder } from './components/views/SectionPlaceholders';
import { analyzeText, analyzePdf } from './services/api';
import type { AnalysisResponse } from './types/analysis';

const VALID_SECTIONS: NavSectionId[] = [
  'inicio',
  'analisis',
  'transformar',
  'materiales',
  'configuracion',
];

function getSectionFromHash(): NavSectionId {
  const raw = window.location.hash.replace(/^#\/?/, '').split('/')[0];
  return VALID_SECTIONS.includes(raw as NavSectionId)
    ? (raw as NavSectionId)
    : 'analisis';
}

export function App() {
  const [activeSection, setActiveSection] = useState<NavSectionId>(getSectionFromHash);
  const [currentStage, setCurrentStage] = useState<AnalysisStage>('upload');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<AnalysisResponse | null>(null);

  useEffect(() => {
    const onHashChange = () => setActiveSection(getSectionFromHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  function handleSelectSection(section: NavSectionId) {
    setActiveSection(section);
    window.location.hash = `#/${section}`;
  }

  async function runAnalysis(task: () => Promise<AnalysisResponse>, fallbackMsg: string) {
    setLoading(true);
    setError(null);
    try {
      const data = await task();
      setResults(data);
      setCurrentStage('results');
    } catch (err) {
      setError(err instanceof Error ? err.message : fallbackMsg);
    } finally {
      setLoading(false);
    }
  }

  function handleResetAnalysis() {
    setResults(null);
    setError(null);
    setCurrentStage('upload');
  }

  return (
    <AppLayout
      activeSection={activeSection}
      onSelectSection={handleSelectSection}
      currentStage={currentStage}
      onSelectStage={setCurrentStage}
      hasResults={Boolean(results)}
      hasAdaptedVersion={false}
    >
      {activeSection !== 'analisis' ? (
        <SectionPlaceholder
          section={activeSection}
          onGoToAnalysis={() => handleSelectSection('analisis')}
        />
      ) : (
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
              onAnalyzePdf={(file, mock) =>
                runAnalysis(() => analyzePdf(file, mock), 'Error al procesar el archivo PDF.')
              }
              onAnalyzeText={(text, mock) =>
                runAnalysis(() => analyzeText(text, mock), 'Error al procesar el texto.')
              }
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
      )}
    </AppLayout>
  );
}

export default App;
