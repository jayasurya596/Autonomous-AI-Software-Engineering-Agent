import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import axios from "axios";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";

const usesCloudRepositoryStorage =
  import.meta.env.VITE_CLOUD_REPOSITORY_STORAGE?.trim().toLowerCase() === "r2";
interface UserProfile {
  uid: string;
  email: string | null;
  full_name: string;
  developer_role: string;
  experience_level: string;
  preferred_languages: string[];
  preferred_model: string | null;
  development_environment: string | null;
  github_username: string | null;
}

interface Workspace {
  id: string;
  name: string;
  description: string;
}

interface Project {
  id: string;
  workspace_id: string;
  name: string;
  purpose: string;
  language: string;
  framework: string | null;
  repository_url: string | null;
}

interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

interface ChangeFile {
  path: string;
  content: string;
  rationale: string;
}

interface AgentExecution {
  execution_id: string;
  project_id: string | null;
  goal: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  cancel_requested: boolean;
  resume_count: number;
  failure_summary: string | null;
  debug_summary: string | null;
  created_at: string;
  traces: Array<{
    id: number;
    agent_name: string;
    status: string;
    summary: string | null;
    warnings: string[];
    evidence_count: number;
  }>;
}

interface Approval {
  approval_id: string;
  project_id: string;
  repository_url: string;
  title: string;
  changes: ChangeFile[];
  diff: string;
  runtime: "python" | "node";
  test_output: string;
  test_duration_ms: number;
  status: "pending" | "publishing" | "published" | "rejected" | "failed";
  github_branch: string | null;
  pull_request_url: string | null;
  failure_summary: string | null;
  created_at: string;
  events: Array<{ actor_uid: string; action: string; summary: string; created_at: string }>;
}

interface ProjectMemory {
  id: string;
  content: string;
  created_at: string;
  similarity: number | null;
}

interface GithubConnection {
  connected: boolean;
  username: string | null;
  scopes: string[];
}

function responseMessage(caught: unknown, fallback: string): string {
  if (axios.isAxiosError(caught)) {
    const detail = caught.response?.data?.detail;
    if (typeof detail === "string") return detail;
    const message = caught.response?.data?.error?.message;
    if (typeof message === "string") return message;
  }
  if (caught instanceof Error && caught.message === PREVIEW_BACKEND_UNAVAILABLE_MESSAGE) {
    return caught.message;
  }
  return fallback;
}

function ProjectWorkbench({ project }: { project: Project }) {
  const [repositoryDirectory, setRepositoryDirectory] = useState("");
  const [repositoryUploaded, setRepositoryUploaded] = useState(false);
  const [repositoryFileCount, setRepositoryFileCount] = useState(0);
  const [goal, setGoal] = useState("");
  const [runtime, setRuntime] = useState<"python" | "node">("python");
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [proposal, setProposal] = useState<{ summary: string; files: ChangeFile[]; acceptance_checks: string[]; risks: string[] } | null>(null);
  const [testResult, setTestResult] = useState<{ status: string; output: string; duration_ms: number } | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [memories, setMemories] = useState<ProjectMemory[]>([]);
  const [memoryText, setMemoryText] = useState("");
  const [memorySearch, setMemorySearch] = useState("");
  const [connection, setConnection] = useState<GithubConnection>({ connected: false, username: null, scopes: [] });
  const [githubToken, setGithubToken] = useState("");
  const [executions, setExecutions] = useState<AgentExecution[]>([]);
  const [executionGoal, setExecutionGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const repositoryEndpoint = `/repositories/projects/${encodeURIComponent(project.id)}`;

  async function refreshApprovals() {
    const { data } = await api.get<{ items: Approval[] }>("/approvals", { params: { project_id: project.id } });
    setApprovals(data.items);
  }

  async function refreshMemories() {
    const params = memorySearch.trim().length >= 2 ? { query: memorySearch.trim() } : {};
    const { data } = await api.get<{ items: ProjectMemory[] }>(`/projects/${encodeURIComponent(project.id)}/memory`, { params });
    setMemories(data.items);
  }

  async function refreshExecutions() {
    const { data } = await api.get<{ items: AgentExecution[] }>("/executions");
    setExecutions(data.items.filter((item) => item.project_id === project.id));
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get<GithubConnection>("/github/connection"),
      api.get<{ items: Approval[] }>("/approvals", { params: { project_id: project.id } }),
      api.get<{ items: ProjectMemory[] }>(`/projects/${encodeURIComponent(project.id)}/memory`),
      api.get<{ items: AgentExecution[] }>("/executions"),
    ]).then(([github, review, memory, execution]) => {
      if (!active) return;
      setConnection(github.data);
      setApprovals(review.data.items);
      setMemories(memory.data.items);
      setExecutions(execution.data.items.filter((item) => item.project_id === project.id));
    }).catch((caught: unknown) => {
      if (active) setMessage(responseMessage(caught, "Some project tools could not load. Check that PostgreSQL and the API are running."));
    });
    if (usesCloudRepositoryStorage) {
      api.get<{ uploaded: boolean; file_count: number }>(`${repositoryEndpoint}/source`)
        .then(({ data }) => {
          if (!active) return;
          setRepositoryUploaded(data.uploaded);
          setRepositoryFileCount(data.file_count);
          setRepositoryDirectory(data.uploaded ? "r2-snapshot" : "");
        })
        .catch((caught: unknown) => {
          if (active) setMessage(responseMessage(caught, "The repository snapshot status could not be loaded."));
        });
    }
    return () => { active = false; };
  }, [project.id]);

  useEffect(() => {
    if (!executions.some((item) => item.status === "queued" || item.status === "running")) return;
    const timer = window.setInterval(() => { void refreshExecutions(); }, 3000);
    return () => window.clearInterval(timer);
  }, [executions, project.id]);

  async function perform(action: () => Promise<void>, success?: string) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      if (success) setMessage(success);
    } catch (caught) {
      setMessage(responseMessage(caught, "The request could not be completed."));
    } finally {
      setBusy(false);
    }
  }

  async function uploadRepository(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    await perform(async () => {
      const { data } = await api.put<{ uploaded: boolean; file_count: number }>(
        `${repositoryEndpoint}/source`,
        file,
        { headers: { "Content-Type": "application/zip" }, timeout: 120_000 },
      );
      setRepositoryUploaded(data.uploaded);
      setRepositoryFileCount(data.file_count);
      setRepositoryDirectory("r2-snapshot");
      setAnalysis(null);
      setProposal(null);
      setTestResult(null);
    }, "Repository snapshot uploaded to Cloudflare.");
    event.target.value = "";
  }

  async function analyzeRepository() {
    await perform(async () => {
      const { data } = await api.post(`${repositoryEndpoint}/analyze`, { repository_directory: repositoryDirectory.trim() });
      setAnalysis(data.analysis.summary);
      setProposal(null);
      setTestResult(null);
      setMessage(`Reviewed ${data.snapshot_file_count} files; ${data.omitted_file_count} were omitted by the safety limits.`);
    });
  }

  async function generateProposal() {
    await perform(async () => {
      const { data } = await api.post(`${repositoryEndpoint}/changes`, { repository_directory: repositoryDirectory.trim(), goal: goal.trim() });
      setAnalysis(data.analysis.summary);
      setProposal(data.proposal);
      setTestResult(null);
    }, "Change proposal is ready for review.");
  }

  async function testProposal() {
    if (!proposal) return;
    await perform(async () => {
      const { data } = await api.post(`${repositoryEndpoint}/test`, { repository_directory: repositoryDirectory.trim(), runtime, changes: proposal.files });
      setTestResult(data);
    }, "Sandbox test finished.");
  }

  async function requestReview() {
    if (!proposal) return;
    await perform(async () => {
      const { data } = await api.post<Approval>(`/projects/${encodeURIComponent(project.id)}/approvals`, {
        repository_directory: repositoryDirectory.trim(),
        title: proposal.summary.slice(0, 300),
        runtime,
        changes: proposal.files,
      });
      setApprovals((current) => [data, ...current]);
      setTestResult({ status: "passed", output: data.test_output, duration_ms: data.test_duration_ms });
    }, "The change passed a fresh sandbox run and was added to the approval queue.");
  }

  async function connectGithub(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await perform(async () => {
      const { data } = await api.put<GithubConnection>("/github/connection", { token: githubToken });
      setConnection(data);
      setGithubToken("");
    }, "GitHub account connected. The token is encrypted before storage.");
  }

  async function disconnectGithub() {
    await perform(async () => {
      await api.delete("/github/connection");
      setConnection({ connected: false, username: null, scopes: [] });
    }, "GitHub account disconnected.");
  }

  async function createExecution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await perform(async () => {
      await api.post("/executions", { project_id: project.id, goal: executionGoal.trim() });
      setExecutionGoal("");
      await refreshExecutions();
    }, "Agent run queued.");
  }

  async function saveMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await perform(async () => {
      await api.post(`/projects/${encodeURIComponent(project.id)}/memory`, { content: memoryText.trim() });
      setMemoryText("");
      await refreshMemories();
    }, "Project note saved.");
  }

  async function decideApproval(approval: Approval, decision: "approve" | "reject") {
    const confirmed = decision === "approve"
      ? window.confirm(`Create a GitHub branch and pull request for “${approval.title}”?`)
      : window.confirm(`Reject “${approval.title}”?`);
    if (!confirmed) return;
    await perform(async () => {
      const { data } = await api.post<Approval>(`/approvals/${approval.approval_id}/${decision}`);
      setApprovals((current) => current.map((item) => item.approval_id === data.approval_id ? data : item));
    }, decision === "approve" ? "GitHub approval finished." : "Change rejected.");
  }

  async function executionAction(execution: AgentExecution, action: "cancel" | "resume") {
    await perform(async () => {
      await api.post(`/executions/${execution.execution_id}/${action}`);
      await refreshExecutions();
    }, action === "cancel" ? "Cancellation requested." : "Execution resumed.");
  }

  return (
    <section className="project-workbench" aria-labelledby="workbench-heading">
      <div className="workbench-heading">
        <div>
          <p className="eyebrow">PROJECT WORKBENCH</p>
          <h2 id="workbench-heading">{project.name}</h2>
          <p className="intro">Run the agents, review proposed code, and choose when to publish a pull request.</p>
        </div>
        <button className="text-button" type="button" onClick={() => { void Promise.all([refreshApprovals(), refreshMemories(), refreshExecutions()]); }}>Refresh project data</button>
      </div>
      {message && <Message kind={message.toLowerCase().includes("could not") || message.toLowerCase().includes("failed") ? "error" : "success"}>{message}</Message>}

      <section className="workbench-section" aria-labelledby="github-heading">
        <h3 id="github-heading">GitHub connection</h3>
        {connection.connected ? (
          <div className="inline-actions"><p>Connected as <strong>{connection.username}</strong>{connection.scopes.length ? ` · ${connection.scopes.join(", ")}` : ""}</p><button className="secondary-button compact-button" type="button" onClick={() => void disconnectGithub()} disabled={busy}>Disconnect</button></div>
        ) : (
          <form className="connection-form" onSubmit={(event) => void connectGithub(event)}>
            <label className="field" htmlFor="github-token"><span>GitHub personal access token</span><input id="github-token" type="password" value={githubToken} onChange={(event) => setGithubToken(event.target.value)} autoComplete="new-password" minLength={20} maxLength={512} required /></label>
            <p className="helper-copy">Use a token with access to this repository and permission to create branches and pull requests. It is validated with GitHub and encrypted in PostgreSQL.</p>
            <button className="secondary-button compact-button" type="submit" disabled={busy || !githubToken}>Connect GitHub</button>
          </form>
        )}
      </section>

      <div className="workbench-grid">
        <section className="workbench-section" aria-labelledby="agent-work-heading">
          <h3 id="agent-work-heading">Repository agent</h3>
          {usesCloudRepositoryStorage ? (
            <div className="field">
              <label htmlFor="repository-zip"><span>Repository ZIP snapshot</span><input id="repository-zip" type="file" accept=".zip,application/zip" onChange={(event) => void uploadRepository(event)} disabled={busy} /></label>
              <p className="helper-copy">Upload a ZIP archive up to 20 MB. The current snapshot is replaced when you upload another. {repositoryUploaded ? `${repositoryFileCount} source files are saved.` : "Upload a snapshot before running repository tools."}</p>
            </div>
          ) : (
            <label className="field" htmlFor="repository-directory"><span>Repository folder under configured local root</span><input id="repository-directory" value={repositoryDirectory} onChange={(event) => setRepositoryDirectory(event.target.value)} placeholder="my-project" maxLength={512} /></label>
          )}
          <div className="inline-actions"><button className="secondary-button compact-button" type="button" onClick={() => void analyzeRepository()} disabled={busy || !repositoryDirectory.trim()}>Analyze repository</button><label className="field runtime-select" htmlFor="test-runtime"><span>Test profile</span><select id="test-runtime" value={runtime} onChange={(event) => setRuntime(event.target.value as "python" | "node")}><option value="python">Python</option><option value="node">Node.js</option></select></label></div>
          {analysis && <p className="analysis-summary">{analysis}</p>}
          <form className="auth-form dashboard-form" onSubmit={(event) => { event.preventDefault(); void generateProposal(); }}>
            <label className="field" htmlFor="change-goal"><span>Requested code change</span><textarea id="change-goal" value={goal} onChange={(event) => setGoal(event.target.value)} minLength={10} maxLength={4000} rows={4} required /></label>
            <button className="primary-button" type="submit" disabled={busy || !repositoryDirectory.trim() || goal.trim().length < 10}>{busy ? "Working…" : "Generate change proposal"}</button>
          </form>
          {proposal && <div className="proposal-card"><h4>{proposal.summary}</h4><ul>{proposal.files.map((file) => <li key={file.path}><strong>{file.path}</strong><span>{file.rationale}</span></li>)}</ul><p>Acceptance checks: {proposal.acceptance_checks.join(" · ")}</p>{proposal.risks.length > 0 && <p>Risks: {proposal.risks.join(" · ")}</p>}<div className="inline-actions"><button className="secondary-button compact-button" type="button" onClick={() => void testProposal()} disabled={busy}>Run isolated tests</button><button className="primary-button compact-button" type="button" onClick={() => void requestReview()} disabled={busy}>Run tests and request approval</button></div><details><summary>Review generated file contents</summary>{proposal.files.map((file) => <div className="code-review" key={file.path}><h5>{file.path}</h5><pre>{file.content}</pre></div>)}</details></div>}
          {testResult && <div className="test-result"><strong>Test status: {testResult.status}</strong><span>{testResult.duration_ms} ms</span><pre>{testResult.output || "No test output."}</pre></div>}
        </section>

        <section className="workbench-section" aria-labelledby="execution-heading">
          <h3 id="execution-heading">Agent runs and traces</h3>
          <form className="auth-form" onSubmit={(event) => void createExecution(event)}><label className="field" htmlFor="execution-goal"><span>Engineering task</span><textarea id="execution-goal" value={executionGoal} onChange={(event) => setExecutionGoal(event.target.value)} minLength={10} maxLength={4000} rows={3} required /></label><button className="primary-button" type="submit" disabled={busy || executionGoal.trim().length < 10}>Start agent run</button></form>
          {executions.length === 0 ? <p className="empty-state">No agent runs for this project yet.</p> : <ul className="record-list">{executions.map((execution) => <li key={execution.execution_id}><div className="record-heading"><strong>{execution.goal}</strong><span className={`status-pill ${execution.status}`}>{execution.status}</span></div><small>{new Date(execution.created_at).toLocaleString()}</small>{execution.failure_summary && <p>{execution.failure_summary}</p>}{execution.debug_summary && <p>{execution.debug_summary}</p>}{execution.traces.length > 0 && <details><summary>{execution.traces.length} specialist traces</summary><ul className="trace-list">{execution.traces.map((trace) => <li key={trace.id}><strong>{trace.agent_name}</strong> · {trace.status}{trace.summary && <p>{trace.summary}</p>}{trace.warnings.map((warning) => <small key={warning}>{warning}</small>)}</li>)}</ul></details>}<div className="inline-actions">{(execution.status === "queued" || execution.status === "running") && <button className="text-button" type="button" onClick={() => void executionAction(execution, "cancel")}>Cancel run</button>}{["failed", "cancelled", "interrupted"].includes(execution.status) && execution.resume_count < 5 && <button className="text-button" type="button" onClick={() => void executionAction(execution, "resume")}>Resume run</button>}</div></li>)}</ul>}
        </section>
      </div>

      <section className="workbench-section" aria-labelledby="approval-heading">
        <div className="section-heading-row"><h3 id="approval-heading">Approval center</h3><span>{approvals.filter((item) => item.status === "pending").length} awaiting review</span></div>
        {approvals.length === 0 ? <p className="empty-state">Tested change sets requested for review will appear here.</p> : <ul className="record-list">{approvals.map((approval) => <li key={approval.approval_id}><div className="record-heading"><strong>{approval.title}</strong><span className={`status-pill ${approval.status}`}>{approval.status}</span></div><small>{new Date(approval.created_at).toLocaleString()} · {approval.runtime} checks · {approval.test_duration_ms} ms</small>{approval.failure_summary && <p>{approval.failure_summary}</p>}<details><summary>Review diff and test output</summary><pre className="diff-view">{approval.diff || "No text diff was generated."}</pre><pre>{approval.test_output || "No test output."}</pre><ul className="trace-list">{approval.events.map((item, index) => <li key={`${item.action}-${index}`}><strong>{item.action}</strong> · {item.summary}</li>)}</ul></details>{approval.pull_request_url && <a className="pull-request-link" href={approval.pull_request_url} target="_blank" rel="noreferrer">Open pull request on GitHub</a>}{approval.status === "pending" && <div className="inline-actions"><button className="primary-button compact-button" type="button" onClick={() => void decideApproval(approval, "approve")} disabled={busy || !connection.connected}>Approve and create pull request</button><button className="secondary-button compact-button" type="button" onClick={() => void decideApproval(approval, "reject")} disabled={busy}>Reject change</button></div>}</li>)}</ul>}
      </section>

      <section className="workbench-section" aria-labelledby="memory-heading">
        <div className="section-heading-row"><h3 id="memory-heading">Project memory</h3><form className="memory-search" onSubmit={(event) => { event.preventDefault(); void refreshMemories(); }}><input aria-label="Search project memory" value={memorySearch} onChange={(event) => setMemorySearch(event.target.value)} placeholder="Search notes" maxLength={1000} /><button className="text-button" type="submit">Search</button></form></div>
        <form className="memory-entry" onSubmit={(event) => void saveMemory(event)}><label className="field" htmlFor="memory-content"><span>Save project note</span><textarea id="memory-content" value={memoryText} onChange={(event) => setMemoryText(event.target.value)} minLength={10} maxLength={4000} rows={2} required /></label><button className="secondary-button compact-button" type="submit" disabled={busy || memoryText.trim().length < 10}>Save note</button></form>
        {memories.length === 0 ? <p className="empty-state">No matching project notes.</p> : <ul className="memory-list">{memories.map((memory) => <li key={memory.id}><p>{memory.content}</p>{memory.similarity !== null && <small>Match score {(memory.similarity * 100).toFixed(0)}%</small>}<button className="text-button" type="button" onClick={() => void perform(async () => { await api.delete(`/projects/${encodeURIComponent(project.id)}/memory/${memory.id}`); await refreshMemories(); }, "Project note deleted.")}>Delete note</button></li>)}</ul>}
      </section>
    </section>
  );
}

const languageOptions = [
  "Python", "TypeScript", "JavaScript", "Java", "Go", "Rust", "C#", "C++", "Swift", "Kotlin", "Other",
];
import { api, PREVIEW_BACKEND_UNAVAILABLE_MESSAGE } from "./api/client";
import { authErrorMessage } from "./auth/errors";
import { useAuth } from "./auth/AuthProvider";
import { isGoogleAuthEnabled } from "./lib/firebase";

const isFirebasePreviewOnly =
  import.meta.env.VITE_FIREBASE_PREVIEW_ONLY?.trim().toLowerCase() === "true";

function PreviewBanner() {
  return (
    <aside className="preview-banner" role="status">
      Free Firebase preview: sign-in and the front end are live. AI runs and code testing need a backend that is not included here.
    </aside>
  );
}

function ConfigNotice() {
  return (
    <div className="config-notice" role="note">
      <strong>Firebase setup is needed</strong>
      <p>
        Add the Firebase web app values to the root <code>.env</code> file. The
        app does not create fake accounts while Firebase is unavailable.
      </p>
    </div>
  );
}

function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="page-shell">
      <section className="foundation-card auth-card">{children}</section>
    </main>
  );
}

function Field({
  label,
  name,
  type = "text",
  autoComplete,
  minLength,
}: {
  label: string;
  name: string;
  type?: string;
  autoComplete?: string;
  minLength?: number;
}) {
  return (
    <label className="field" htmlFor={name}>
      <span>{label}</span>
      <input
        id={name}
        name={name}
        type={type}
        autoComplete={autoComplete}
        minLength={minLength}
        required
      />
    </label>
  );
}

function Message({ children, kind = "error" }: { children: ReactNode; kind?: "error" | "success" }) {
  return <p className={`form-message ${kind}`} role="status">{children}</p>;
}

function useAuthPage() {
  const { configured, loading, user, signIn, register, signInWithGoogle, requestPasswordReset, resendVerification, refreshUser, logOut } = useAuth();
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);

  async function perform(action: () => Promise<unknown>) {
    setError("");
    setSuccess("");
    setBusy(true);
    try {
      await action();
    } catch (caught) {
      setError(authErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return {
    configured,
    loading,
    user,
    signIn,
    register,
    signInWithGoogle,
    requestPasswordReset,
    resendVerification,
    refreshUser,
    logOut,
    error,
    setError,
    success,
    setSuccess,
    busy,
    perform,
  };
}

function LoginPage() {
  const auth = useAuthPage();
  const navigate = useNavigate();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await auth.perform(async () => {
      const user = await auth.signIn(String(data.get("email")), String(data.get("password")));
      navigate(user.emailVerified ? "/onboarding" : "/verify-email");
    });
  }

  async function googleSignIn() {
    await auth.perform(async () => {
      const user = await auth.signInWithGoogle();
      navigate(user.emailVerified ? "/onboarding" : "/verify-email");
    });
  }

  return (
    <AuthLayout>
      <p className="eyebrow">AUTONOMOUS AI SOFTWARE ENGINEERING AGENT</p>
      <h1 id="page-title">Sign in</h1>
      <p className="intro">Sign in to continue to your engineering workspace.</p>
      {!auth.configured && <ConfigNotice />}
      <form className="auth-form" onSubmit={submit}>
        <Field label="Email" name="email" type="email" autoComplete="email" />
        <Field label="Password" name="password" type="password" autoComplete="current-password" />
        {auth.error && <Message>{auth.error}</Message>}
        <button className="primary-button" type="submit" disabled={auth.busy || !auth.configured}>
          {auth.busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {auth.configured && isGoogleAuthEnabled && (
        <button className="secondary-button" type="button" onClick={googleSignIn} disabled={auth.busy}>
          Continue with Google
        </button>
      )}
      <nav className="auth-links" aria-label="Account options">
        <Link to="/register">Create an account</Link>
        <Link to="/forgot-password">Forgot password?</Link>
      </nav>
    </AuthLayout>
  );
}

function RegisterPage() {
  const auth = useAuthPage();
  const navigate = useNavigate();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password"));
    if (password.length < 8) {
      auth.setError("Choose a password with at least 8 characters.");
      return;
    }
    await auth.perform(async () => {
      await auth.register(String(data.get("name")), String(data.get("email")), password);
      navigate("/verify-email");
    });
  }

  return (
    <AuthLayout>
      <p className="eyebrow">ACCOUNT SETUP</p>
      <h1 id="page-title">Create your account</h1>
      <p className="intro">We’ll send a verification link to your email address.</p>
      {!auth.configured && <ConfigNotice />}
      <form className="auth-form" onSubmit={submit}>
        <Field label="Full name" name="name" autoComplete="name" />
        <Field label="Email" name="email" type="email" autoComplete="email" />
        <Field label="Password" name="password" type="password" autoComplete="new-password" minLength={8} />
        {auth.error && <Message>{auth.error}</Message>}
        <button className="primary-button" type="submit" disabled={auth.busy || !auth.configured}>
          {auth.busy ? "Creating account…" : "Create account"}
        </button>
      </form>
      <p className="auth-footer">Already registered? <Link to="/login">Sign in</Link></p>
    </AuthLayout>
  );
}

function ForgotPasswordPage() {
  const auth = useAuthPage();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await auth.perform(async () => {
      await auth.requestPasswordReset(String(data.get("email")));
      auth.setSuccess("If the account exists, Firebase has sent a password reset email.");
    });
  }

  return (
    <AuthLayout>
      <p className="eyebrow">ACCOUNT RECOVERY</p>
      <h1 id="page-title">Reset your password</h1>
      <p className="intro">Enter the email address attached to your account.</p>
      {!auth.configured && <ConfigNotice />}
      <form className="auth-form" onSubmit={submit}>
        <Field label="Email" name="email" type="email" autoComplete="email" />
        {auth.error && <Message>{auth.error}</Message>}
        {auth.success && <Message kind="success">{auth.success}</Message>}
        <button className="primary-button" type="submit" disabled={auth.busy || !auth.configured}>
          {auth.busy ? "Sending…" : "Send reset email"}
        </button>
      </form>
      <p className="auth-footer"><Link to="/login">Back to sign in</Link></p>
    </AuthLayout>
  );
}

function VerifyEmailPage() {
  const auth = useAuthPage();
  const navigate = useNavigate();
  if (!auth.loading && !auth.user) return <Navigate to="/login" replace />;

  async function checkVerification() {
    await auth.perform(async () => {
      const user = await auth.refreshUser();
      if (user?.emailVerified) navigate("/onboarding");
      else auth.setError("The address is not verified yet. Open the link from your email and try again.");
    });
  }

  return (
    <AuthLayout>
      <p className="eyebrow">EMAIL VERIFICATION</p>
      <h1 id="page-title">Check your inbox</h1>
      <p className="intro">Verify {auth.user?.email ?? "your email address"} before continuing.</p>
      {auth.error && <Message>{auth.error}</Message>}
      {auth.success && <Message kind="success">{auth.success}</Message>}
      <div className="auth-form">
        <button className="primary-button" type="button" onClick={checkVerification} disabled={auth.busy}>
          I’ve verified my email
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => void auth.perform(async () => {
            await auth.resendVerification();
            auth.setSuccess("A new verification email was sent.");
          })}
          disabled={auth.busy || !auth.user}
        >
          Resend verification email
        </button>
        <button className="text-button" type="button" onClick={() => void auth.logOut()}>
          Sign out
        </button>
      </div>
    </AuthLayout>
  );
}

function RequireVerifiedUser({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.loading) return <AuthLayout><p role="status">Checking your session…</p></AuthLayout>;
  if (!auth.user) return <Navigate to="/login" replace />;
  if (!auth.user.emailVerified) return <Navigate to="/verify-email" replace />;
  return children;
}

function OnboardingPage() {
  const { user } = useAuth();
  const [profile, setProfile] = useState({
    full_name: user?.displayName ?? "",
    developer_role: "developer",
    experience_level: "intermediate",
    preferred_languages: ["Python"],
    preferred_model: "",
    development_environment: "",
    github_username: "",
  });
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    api.get<UserProfile>("/users/profile")
      .then(({ data }) => {
        if (active) {
          setProfile({
            full_name: data.full_name,
            developer_role: data.developer_role,
            experience_level: data.experience_level,
            preferred_languages: data.preferred_languages,
            preferred_model: data.preferred_model ?? "",
            development_environment: data.development_environment ?? "",
            github_username: data.github_username ?? "",
          });
        }
      })
      .catch((caught) => {
        if (!active) return;
        if (axios.isAxiosError(caught) && caught.response?.status === 404) return;
        setError(responseMessage(caught, "Could not load your profile. Please try again."));
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => { active = false; };
  }, []);

  function updateField(
    field: "full_name" | "developer_role" | "experience_level" | "preferred_model" | "development_environment" | "github_username",
    value: string,
  ) {
    setProfile((current) => ({ ...current, [field]: value }));
  }

  function toggleLanguage(language: string) {
    setProfile((current) => ({
      ...current,
      preferred_languages: current.preferred_languages.includes(language)
        ? current.preferred_languages.filter((item) => item !== language)
        : [...current.preferred_languages, language],
    }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    setBusy(true);
    setError("");
    try {
      await api.put("/users/profile", {
        ...profile,
        full_name: profile.full_name.trim(),
        preferred_model: profile.preferred_model.trim() || null,
        development_environment: profile.development_environment.trim() || null,
        github_username: profile.github_username.trim() || null,
      });
      navigate("/app");
    } catch (caught) {
      setError(responseMessage(caught, "Could not save your profile. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <p className="eyebrow">FIRST-TIME SETUP</p>
      <h1 id="page-title">Welcome</h1>
      <p className="intro">Set your developer profile. You can update these details later.</p>
      {error && <Message>{error}</Message>}
      {!loaded && <p role="status">Loading your profile…</p>}
      <form className="auth-form" onSubmit={submit}>
        <label className="field" htmlFor="display-name">
          <span>Full name</span>
          <input id="display-name" value={profile.full_name} onChange={(event) => updateField("full_name", event.target.value)} required minLength={2} maxLength={120} autoComplete="name" />
        </label>
        <label className="field" htmlFor="developer-role">
          <span>Developer role</span>
          <select id="developer-role" value={profile.developer_role} onChange={(event) => updateField("developer_role", event.target.value)}>
            <option value="student">Student</option>
            <option value="developer">Developer</option>
            <option value="ai_engineer">AI engineer</option>
            <option value="technical_lead">Technical lead</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label className="field" htmlFor="experience-level">
          <span>Experience level</span>
          <select id="experience-level" value={profile.experience_level} onChange={(event) => updateField("experience_level", event.target.value)}>
            <option value="beginner">Beginner</option>
            <option value="intermediate">Intermediate</option>
            <option value="advanced">Advanced</option>
          </select>
        </label>
        <fieldset className="language-fieldset">
          <legend>Preferred programming languages</legend>
          <div className="language-grid">
            {languageOptions.map((language) => (
              <label className="language-option" key={language}>
                <input
                  type="checkbox"
                  checked={profile.preferred_languages.includes(language)}
                  onChange={() => toggleLanguage(language)}
                />
                <span>{language}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field" htmlFor="preferred-model">
          <span>Preferred local AI model <span className="optional-label">Optional</span></span>
          <input id="preferred-model" value={profile.preferred_model} onChange={(event) => updateField("preferred_model", event.target.value)} maxLength={100} />
        </label>
        <label className="field" htmlFor="development-environment">
          <span>Development environment <span className="optional-label">Optional</span></span>
          <input id="development-environment" value={profile.development_environment} onChange={(event) => updateField("development_environment", event.target.value)} maxLength={200} placeholder="OS and editor" />
        </label>
        <label className="field" htmlFor="github-username">
          <span>GitHub username <span className="optional-label">Optional</span></span>
          <input id="github-username" value={profile.github_username} onChange={(event) => updateField("github_username", event.target.value)} maxLength={39} autoComplete="username" />
        </label>
        <button className="primary-button" type="submit" disabled={busy || !loaded || profile.preferred_languages.length === 0}>
          {busy ? "Saving…" : "Save profile"}
        </button>
      </form>
    </AuthLayout>
  );
}

function WorkspaceHome() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceDescription, setWorkspaceDescription] = useState("");
  const workspaceIdempotency = useRef<{ signature: string; key: string } | null>(null);
  const [projectName, setProjectName] = useState("");
  const [projectPurpose, setProjectPurpose] = useState("");
  const [projectLanguage, setProjectLanguage] = useState("Python");
  const [projectFramework, setProjectFramework] = useState("");
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const projectIdempotency = useRef<{ signature: string; key: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get<UserProfile>("/users/profile"),
      api.get<Page<Workspace>>("/workspaces?page_size=100"),
      api.get<Page<Project>>("/projects?page_size=100"),
    ])
      .then(([profileResponse, workspaceResponse, projectResponse]) => {
        if (!active) return;
        setProfile(profileResponse.data);
        setWorkspaces(workspaceResponse.data.items);
        setProjects(projectResponse.data.items);
        setSelectedWorkspaceId((current) => current || workspaceResponse.data.items[0]?.id || "");
      })
      .catch((caught) => {
        if (!active) return;
        if (axios.isAxiosError(caught) && caught.response?.status === 404) {
          navigate("/onboarding");
          return;
        }
        setError("Could not load your workspace data. Check the API and Firestore emulator.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [navigate]);

  async function submitWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const workspacePayload = {
      name: workspaceName.trim(),
      description: workspaceDescription.trim(),
    };
    const workspaceSignature = JSON.stringify(workspacePayload);
    const previousWorkspaceRequest = workspaceIdempotency.current;
    const workspaceKey = previousWorkspaceRequest?.signature === workspaceSignature
      ? previousWorkspaceRequest.key
      : crypto.randomUUID();
    workspaceIdempotency.current = { signature: workspaceSignature, key: workspaceKey };
    try {
      const { data } = await api.post<Workspace>(
        "/workspaces",
        workspacePayload,
        { headers: { "Idempotency-Key": workspaceKey } },
      );
      setWorkspaces((current) => [...current, data]);
      setSelectedWorkspaceId(data.id);
      setWorkspaceName("");
      setWorkspaceDescription("");
      workspaceIdempotency.current = null;
    } catch {
      setError("Could not create the workspace. Check the form and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function submitProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedWorkspaceId) return;
    setBusy(true);
    setError("");
    const projectPayload = {
      workspace_id: selectedWorkspaceId,
      name: projectName.trim(),
      purpose: projectPurpose.trim(),
      language: projectLanguage,
      framework: projectFramework.trim() || null,
      repository_url: repositoryUrl.trim() || null,
    };
    const projectSignature = JSON.stringify(projectPayload);
    const previousProjectRequest = projectIdempotency.current;
    const projectKey = previousProjectRequest?.signature === projectSignature
      ? previousProjectRequest.key
      : crypto.randomUUID();
    projectIdempotency.current = { signature: projectSignature, key: projectKey };
    try {
      const { data } = await api.post<Project>(
        "/projects",
        projectPayload,
        { headers: { "Idempotency-Key": projectKey } },
      );
      setProjects((current) => [data, ...current]);
      setProjectName("");
      setProjectPurpose("");
      setProjectFramework("");
      setRepositoryUrl("");
      projectIdempotency.current = null;
    } catch {
      setError("Could not create the project. Confirm the selected workspace and try again.");
    } finally {
      setBusy(false);
    }
  }

  const visibleProjects = projects.filter((project) => project.workspace_id === selectedWorkspaceId);
  const selectedProject = visibleProjects.find((project) => project.id === selectedProjectId) ?? visibleProjects[0] ?? null;

  return (
    <main className="page-shell dashboard-shell">
      <section className="foundation-card workspace-card" aria-labelledby="page-title">
        <div className="dashboard-heading">
          <div>
            <p className="eyebrow">YOUR DEVELOPMENT SPACE</p>
            <h1 id="page-title">Welcome, {profile?.full_name || auth.user?.displayName || auth.user?.email}</h1>
            <p className="intro">Create a workspace, then add the projects you want to work on.</p>
          </div>
          <button className="text-button" type="button" onClick={() => void auth.logOut()}>Sign out</button>
        </div>
        {error && <Message>{error}</Message>}
        {loading ? <p role="status">Loading your workspaces…</p> : (
          <div className="dashboard-grid">
            <section className="dashboard-section" aria-labelledby="workspace-heading">
              <h2 id="workspace-heading">Workspaces</h2>
              {workspaces.length === 0 ? <p className="empty-state">Your workspaces will appear here.</p> : (
                <div className="workspace-list">
                  {workspaces.map((workspace) => (
                    <button
                      className={`workspace-choice ${selectedWorkspaceId === workspace.id ? "selected" : ""}`}
                      key={workspace.id}
                      type="button"
                      onClick={() => setSelectedWorkspaceId(workspace.id)}
                    >
                      <strong>{workspace.name}</strong>
                      <span>{workspace.description || "No description"}</span>
                    </button>
                  ))}
                </div>
              )}
              <form className="auth-form dashboard-form" onSubmit={submitWorkspace}>
                <h3>Create a workspace</h3>
                <label className="field" htmlFor="workspace-name">
                  <span>Name</span>
                  <input id="workspace-name" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} required minLength={2} maxLength={80} />
                </label>
                <label className="field" htmlFor="workspace-description">
                  <span>Description <span className="optional-label">Optional</span></span>
                  <textarea id="workspace-description" value={workspaceDescription} onChange={(event) => setWorkspaceDescription(event.target.value)} maxLength={500} rows={3} />
                </label>
                <button className="primary-button" type="submit" disabled={busy}>{busy ? "Saving…" : "Create workspace"}</button>
              </form>
            </section>
            <section className="dashboard-section" aria-labelledby="projects-heading">
              <h2 id="projects-heading">Projects</h2>
              {!selectedWorkspaceId ? <p className="empty-state">Create a workspace before adding a project.</p> : (
                <>
                  {visibleProjects.length === 0 ? <p className="empty-state">No projects in this workspace yet.</p> : (
                    <ul className="project-list">
                      {visibleProjects.map((project) => (
                        <li className="project-item" key={project.id}>
                          <button className={`project-select ${selectedProject?.id === project.id ? "selected" : ""}`} type="button" onClick={() => setSelectedProjectId(project.id)} aria-pressed={selectedProject?.id === project.id}>
                            <strong>{project.name}</strong>
                            <span>{project.language}{project.framework ? ` · ${project.framework}` : ""}</span>
                            <p>{project.purpose}</p>
                          </button>
                          {project.repository_url && <a href={project.repository_url} target="_blank" rel="noreferrer">Open repository</a>}
                        </li>
                      ))}
                    </ul>
                  )}
                  <form className="auth-form dashboard-form" onSubmit={submitProject}>
                    <h3>Add a project</h3>
                    <label className="field" htmlFor="project-name">
                      <span>Name</span>
                      <input id="project-name" value={projectName} onChange={(event) => setProjectName(event.target.value)} required minLength={2} maxLength={100} />
                    </label>
                    <label className="field" htmlFor="project-purpose">
                      <span>Purpose</span>
                      <textarea id="project-purpose" value={projectPurpose} onChange={(event) => setProjectPurpose(event.target.value)} required maxLength={1000} rows={3} />
                    </label>
                    <label className="field" htmlFor="project-language">
                      <span>Language</span>
                      <select id="project-language" value={projectLanguage} onChange={(event) => setProjectLanguage(event.target.value)}>
                        {languageOptions.map((language) => <option key={language} value={language}>{language}</option>)}
                      </select>
                    </label>
                    <label className="field" htmlFor="project-framework">
                      <span>Framework <span className="optional-label">Optional</span></span>
                      <input id="project-framework" value={projectFramework} onChange={(event) => setProjectFramework(event.target.value)} maxLength={64} />
                    </label>
                    <label className="field" htmlFor="repository-url">
                      <span>Repository URL <span className="optional-label">Optional</span></span>
                      <input id="repository-url" type="url" value={repositoryUrl} onChange={(event) => setRepositoryUrl(event.target.value)} maxLength={2048} placeholder="https://github.com/owner/repository" />
                    </label>
                    <button className="primary-button" type="submit" disabled={busy}>{busy ? "Saving…" : "Create project"}</button>
                  </form>
                </>
              )}
            </section>
          </div>
        )}
        {!loading && selectedProject && <ProjectWorkbench key={selectedProject.id} project={selectedProject} />}
      </section>
    </main>
  );
}

function App() {
  return (
    <>
      {isFirebasePreviewOnly && <PreviewBanner />}
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/onboarding" element={<RequireVerifiedUser><OnboardingPage /></RequireVerifiedUser>} />
        <Route path="/app" element={<RequireVerifiedUser><WorkspaceHome /></RequireVerifiedUser>} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </>
  );
}

export default App;
