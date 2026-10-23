// Clerk simulado para las capturas de desarrollo (npm run capturas): sesión iniciada, sin red.
// No forma parte del build: vite.capturas.config.ts lo usa en lugar de @clerk/react.
import type { ReactNode } from "react";

export function ClerkProvider({ children }: { children: ReactNode }) {
    return <>{children}</>;
}

export function Show({ when, children }: { when: string; children: ReactNode }) {
    return when === "signed-in" ? <>{children}</> : null;
}

export const ClerkLoaded = ({ children }: { children: ReactNode }) => <>{children}</>;
export const ClerkLoading = () => null;
export const SignIn = () => <div style={{ padding: 24, border: "1px dashed #ccc", borderRadius: 12 }}>Componente de Clerk</div>;
export const SignOutButton = ({ children }: { children: ReactNode }) => <>{children}</>;
export const UserButton = () => null;

export function useAuth() {
    return { getToken: async () => "token-de-capturas" };
}

export function useClerk() {
    return { signOut: async () => undefined };
}
