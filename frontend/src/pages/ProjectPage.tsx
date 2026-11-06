import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type {
  ChangeRequest,
  DocumentVersion,
  Project,
  RequirementSet,
  Snapshot,
} from "../types";

function UploadButton({ label, accept, onFile }: {
  label: string;
  accept: string;
  onFile: (file: File) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <label className={`button ${busy ? "disabled" : ""}`}>
      {busy ? "Import en cours…" : label}
      <input
        type="file"
        accept={accept}
        hidden
        disabled={busy}
        onChange={async (event) => {
          const file = event.target.files?.[0];
          event.target.value = ""; // lets the same file be chosen again
          if (!file) return;
          setBusy(true);
          await onFile(file);
          setBusy(false);
        }}
      />
    </label>
  );
}

export default function ProjectPage() {
  const projectId = useParams().id!;
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [documents, setDocuments] = useState<DocumentVersion[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [sets, setSets] = useState<RequirementSet[]>([]);
  const [changes, setChanges] = useState<ChangeRequest[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [title, setTitle] = useState("");
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");

  const base = `/projects/${projectId}`;
  const load = useCallback(async () => {
    const [p, d, r, s, c] = await Promise.all([
      api.get<Project>(base),
      api.get<DocumentVersion[]>(`${base}/documents`),
      api.get<Snapshot[]>(`${base}/repository-snapshots`),
      api.get<RequirementSet[]>(`${base}/requirement-sets`),
      api.get<ChangeRequest[]>(`${base}/change-requests`),
    ]);
    setProject(p);
    setDocuments(d);
    setSnapshots(r);
    setSets(s);
    setChanges(c);
  }, [base]);

  useEffect(() => {
    load().catch((e: Error) => setError(e.message));
  }, [load]);

  const upload = (path: string, describe: (body: never) => string) => async (file: File) => {
    setError("");
    setMessage("");
    try {
      const body = await api.upload<never>(`${base}/${path}`, file);
      setMessage(describe(body));
      await load();
    } catch (err) {
      setError(`${file.name} : ${(err as Error).message}`);
    }
  };

  async function createChange(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const created = await api.post<ChangeRequest>(`${base}/change-requests`, {
        title,
        set_before_id: Number(before),
        set_after_id: Number(after),
      });
      navigate(`/change-requests/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const canImport = project?.my_role === "ADMIN" || project?.my_role === "ANALYST";
  const latestSnapshot = snapshots[snapshots.length - 1];

  return (
    <>
      <p className="crumbs"><Link to="/">Projets</Link> /</p>
      <div className="title-row">
        <h1>{project?.name ?? "…"}</h1>
        <Link to={`${base}/audit`}>Historique d'audit</Link>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="notice" role="status">{message}</p>}

      <section>
        <div className="title-row">
          <h2>Documents</h2>
          {canImport && (
            <UploadButton
              label="Importer un document"
              accept=".md,.txt,.pdf"
              onFile={upload("documents", (b: DocumentVersion) =>
                b.already_imported
                  ? `${b.name} : ${b.message}`
                  : `${b.name} : version ${b.number}, ${b.segment_count} segments.`,
              )}
            />
          )}
        </div>
        {documents.length === 0 && <p className="muted">Aucun document importé.</p>}
        <ul className="rows">
          {documents.map((d) => (
            <li key={d.version_id}>
              <span>{d.name} <span className="muted">v{d.number}</span></span>
              <span className="muted">{d.segment_count} segments</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="title-row">
          <h2>Code Python</h2>
          {canImport && (
            <UploadButton
              label="Importer une archive .zip"
              accept=".zip"
              onFile={upload("repository-snapshots", (b: Snapshot) =>
                b.already_imported
                  ? `${b.archive_name} : déjà importé.`
                  : `${b.archive_name} : ${b.python_file_count} fichiers Python analysés.`,
              )}
            />
          )}
        </div>
        {!latestSnapshot && <p className="muted">Aucune archive importée.</p>}
        {latestSnapshot && (
          <>
            <ul className="rows">
              <li>
                <span>{latestSnapshot.archive_name}</span>
                <span className="muted">{latestSnapshot.python_file_count} fichiers Python</span>
              </li>
            </ul>
            {/* Section 3, step 4: limits are shown, not hidden. */}
            <p className="muted">
              {latestSnapshot.unresolved_call_count} appels non résolus par l'analyse statique ·{" "}
              {latestSnapshot.parse_error_count} fichiers illisibles ·{" "}
              {latestSnapshot.excluded_sensitive_count} fichiers sensibles exclus
            </p>
            {latestSnapshot.warnings.map((warning) => (
              <p className="warning" key={warning}>{warning}</p>
            ))}
          </>
        )}
      </section>

      <section>
        <div className="title-row">
          <h2>Versions d'exigences</h2>
          {canImport && (
            <UploadButton
              label="Importer des exigences"
              accept=".yaml,.yml,.md"
              onFile={upload("requirement-sets", (b: RequirementSet & { already_imported: boolean }) =>
                b.already_imported
                  ? `${b.source_name} : déjà importé (version ${b.number}).`
                  : `Version ${b.number} : ${b.requirement_count} exigences.`,
              )}
            />
          )}
        </div>
        {sets.length === 0 && <p className="muted">Aucune version importée.</p>}
        <ul className="rows">
          {sets.map((s) => (
            <li key={s.id}>
              <span>Version {s.number} <span className="muted">{s.source_name}</span></span>
              <span className="muted">{s.requirement_count} exigences</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Demandes de changement</h2>
        {changes.length === 0 && <p className="muted">Aucune demande pour l'instant.</p>}
        <ul className="rows">
          {changes.map((c) => (
            <li key={c.id}>
              <Link to={`/change-requests/${c.id}`}>{c.title}</Link>
              <span className="tag">{c.status}</span>
            </li>
          ))}
        </ul>
        {canImport && sets.length >= 2 && (
          <form className="inline" onSubmit={createChange}>
            <input
              placeholder="Titre de la demande"
              aria-label="Titre de la demande"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
            <select aria-label="Version avant" value={before} onChange={(e) => setBefore(e.target.value)} required>
              <option value="">Avant…</option>
              {sets.map((s) => <option key={s.id} value={s.id}>Version {s.number}</option>)}
            </select>
            <select aria-label="Version après" value={after} onChange={(e) => setAfter(e.target.value)} required>
              <option value="">Après…</option>
              {sets.map((s) => <option key={s.id} value={s.id}>Version {s.number}</option>)}
            </select>
            <button className="primary">Créer</button>
          </form>
        )}
        {canImport && sets.length < 2 && (
          <p className="muted">Importez deux versions d'exigences pour créer une demande.</p>
        )}
      </section>
    </>
  );
}
