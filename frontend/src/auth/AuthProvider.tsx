import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  GoogleAuthProvider,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  updateProfile,
  type User,
} from "firebase/auth";
import { firebaseAuth, isFirebaseConfigured } from "../lib/firebase";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  configured: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  register: (name: string, email: string, password: string) => Promise<User>;
  signInWithGoogle: () => Promise<User>;
  requestPasswordReset: (email: string) => Promise<void>;
  resendVerification: () => Promise<void>;
  refreshUser: () => Promise<User | null>;
  logOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!firebaseAuth) {
      setLoading(false);
      return;
    }
    return onAuthStateChanged(firebaseAuth, (nextUser) => {
      setUser(nextUser);
      setLoading(false);
    });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      configured: isFirebaseConfigured,
      async signIn(email, password) {
        if (!firebaseAuth) throw new Error("Firebase is not configured.");
        const result = await signInWithEmailAndPassword(firebaseAuth, email, password);
        setUser(result.user);
        return result.user;
      },
      async register(name, email, password) {
        if (!firebaseAuth) throw new Error("Firebase is not configured.");
        const result = await createUserWithEmailAndPassword(firebaseAuth, email, password);
        await updateProfile(result.user, { displayName: name.trim() });
        await sendEmailVerification(result.user);
        setUser(result.user);
        return result.user;
      },
      async signInWithGoogle() {
        if (!firebaseAuth) throw new Error("Firebase is not configured.");
        const result = await signInWithPopup(firebaseAuth, new GoogleAuthProvider());
        setUser(result.user);
        return result.user;
      },
      async requestPasswordReset(email) {
        if (!firebaseAuth) throw new Error("Firebase is not configured.");
        await sendPasswordResetEmail(firebaseAuth, email);
      },
      async resendVerification() {
        if (!firebaseAuth?.currentUser) throw new Error("No signed-in user was found.");
        await sendEmailVerification(firebaseAuth.currentUser);
      },
      async refreshUser() {
        if (!firebaseAuth?.currentUser) return null;
        await firebaseAuth.currentUser.reload();
        const refreshedUser = firebaseAuth.currentUser;
        if (refreshedUser) await refreshedUser.getIdToken(true);
        setUser(refreshedUser);
        return refreshedUser;
      },
      async logOut() {
        if (firebaseAuth) await signOut(firebaseAuth);
        setUser(null);
      },
    }),
    [loading, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider.");
  return value;
}
