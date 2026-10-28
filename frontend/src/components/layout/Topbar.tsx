import { Menu, ChevronDown, GraduationCap } from 'lucide-react';

export type AnalysisStage = 'upload' | 'results' | 'adapted';

export interface UserProfileInfo {
  name: string;
  role: string;
  avatarUrl?: string;
}

export interface TopbarProps {
  currentStage: AnalysisStage;
  onSelectStage?: (stage: AnalysisStage) => void;
  hasResults?: boolean;
  hasAdaptedVersion?: boolean;
  showStages?: boolean;
  sectionTitle?: string;
  user?: UserProfileInfo;
  isSidebarOpen: boolean;
  onToggleSidebar: () => void;
}

interface StageItem {
  id: AnalysisStage;
  label: string;
}

const STAGES: StageItem[] = [
  { id: 'upload', label: 'INICIAR ANÁLISIS' },
  { id: 'results', label: 'ANÁLISIS ORIENTATIVO' },
  { id: 'adapted', label: 'VERSIÓN ADAPTADA' },
];

const DEFAULT_USER: UserProfileInfo = {
  name: 'Luciana Maccarini',
  role: 'Docente',
};

export function Topbar({
  currentStage,
  onSelectStage,
  hasResults = false,
  hasAdaptedVersion = false,
  showStages = true,
  sectionTitle,
  user = DEFAULT_USER,
  isSidebarOpen,
  onToggleSidebar,
}: TopbarProps) {
  function isStageEnabled(stageId: AnalysisStage): boolean {
    if (stageId === 'upload') return true;
    if (stageId === 'results') return hasResults || currentStage === 'results' || currentStage === 'adapted';
    if (stageId === 'adapted') return hasAdaptedVersion || currentStage === 'adapted';
    return false;
  }

  return (
    <header className="via-topbar">
      <div className="via-topbar-left">
        <button
          type="button"
          className="via-mobile-menu-btn"
          onClick={onToggleSidebar}
          aria-expanded={isSidebarOpen}
          aria-controls="via-sidebar"
          aria-label="Abrir menú de navegación"
        >
          <Menu size={22} />
        </button>

        {showStages ? (
          <nav className="via-stage-nav" aria-label="Etapas del análisis">
            <ol className="via-stage-list">
              {STAGES.map((stage) => {
                const isActive = currentStage === stage.id;
                const enabled = isStageEnabled(stage.id);

                // En Figma, las etapas futuras que aún no están habilitadas se muestran
                // atenuadas o se revelan a medida que avanza el flujo. Mostramos las 3 etapas
                // del flujo para dar visibilidad completa de los pasos (Criterio de aceptación #143).
                return (
                  <li key={stage.id} className="via-stage-item">
                    <button
                      type="button"
                      className={`via-stage-tab ${isActive ? 'active' : ''} ${
                        !enabled ? 'disabled' : ''
                      }`}
                      aria-current={isActive ? 'step' : undefined}
                      disabled={!enabled}
                      onClick={() => {
                        if (enabled && onSelectStage) {
                          onSelectStage(stage.id);
                        }
                      }}
                    >
                      {stage.label}
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>
        ) : (
          <div className="via-topbar-section-title">
            <span>{sectionTitle}</span>
          </div>
        )}
      </div>

      <div className="via-topbar-right">
        <button
          type="button"
          className="via-user-profile"
          aria-label={`Perfil de ${user.name}, rol ${user.role}`}
        >
          <div className="via-user-avatar" aria-hidden="true">
            {user.avatarUrl ? (
              <img src={user.avatarUrl} alt={user.name} />
            ) : (
              <GraduationCap size={20} strokeWidth={2.2} />
            )}
          </div>
          <div className="via-user-info">
            <span className="via-user-name">{user.name}</span>
            <span className="via-user-role">{user.role}</span>
          </div>
          <ChevronDown size={15} className="via-user-chevron" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
