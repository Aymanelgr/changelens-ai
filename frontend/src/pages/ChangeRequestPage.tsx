import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type {
  ChangeFact,
  ChangeRequest,
  Evidence,
  Finding,
  Project,
  RequirementChange,
  Run,
  TestSuggestion,
} from "../types";

const POLL_MS = 1500;

function factLabel(fact: ChangeFact): string {
  const operator = fact.operator ? `, opérateur « ${fact.operator} »` : "";
  switch (fact.type) {
    case "THRESHOLD_CHANGED":
      return `Seuil ${fact.old} → ${fact.new} ${fact.unit ?? ""}${operator} ${
        fact.operator_changed ? `(était « ${fact.old_operator} »)` : "inchangé"
      }`;
    case "OPERATOR_CHANGED":
      return `Opérateur « ${fact.old_operator} » → « ${fact.operator} » sur ${fact.new}`;
    case "UNIT_CHANGED":
      return `Unité modifiée sur ${fact.new}`;
    case "QUANTITY_ADDED":
      return `Valeur ajoutée : ${fact.new} ${fact.unit ?? ""}`;
    case "QUANTITY_REMOVED":
      return `Valeur retirée : ${fact.old}`;
    case "MODALITY_CHANGED":
      return `Modalité : ${fact.old ?? "aucune"} → ${fact.new ?? "aucune"}`;
    default:
      return "Texte modifié, aucun fait vérifiable";
  }
}

function targetLabel(finding: Finding): string {
  const target = finding.target as {
    file?: string;
    symbol?: string;
    document?: string;
    section?: string;
    requirement?: string;
  };
  if (target.file) return `${target.file} · ${target.symbol}`;
  if (target.document) return `${target.document}${target.section ? ` · ${target.section}` : ""}`;
  return target.requirement ?? finding.target_key;
}

function evidenceLocation(evidence: Evidence): string {
  if (evidence.source_type === "SYMBOL") {
    return `${evidence.file_path}, ligne ${evidence.start_line}`;
  }
  if (evidence.source_type === "SEGMENT") {
    return `${evidence.document_name}${evidence.section_path ? ` · ${evidence.section_path}` : ""}`;
  }
  return "Texte de l'exigence";
}

/** The source with the quoted passage highlighted in place. */
function EvidenceView({ evidence }: { evidence: Evidence }) {
  const start = evidence.context_start_line;
  let body;
  if (start !== null) {
    body = evidence.context.split(/\r\n|\r|\n/).map((line, index) => {
      const number = start + index;
      return (
        <div key={number} className={number === evidence.start_line ? "code-line hit" : "code-line"}>
          <span className="line-number">{number}</span>
          {line || " "}
        </div>
      );
    });
  } else {
    const at = evidence.context.indexOf(evidence.quote);
    body =
      at < 0 ? evidence.context : (
        <>
          {evidence.context.slice(0, at)}
          <mark>{evidence.quote}</mark>
          {evidence.context.slice(at + evidence.quote.length)}
        </>
      );
  }
  return (
    <figure className={`evidence ${evidence.role === "CONTRADICTS" ? "contradicts" : ""}`}>
      <figcaption>
        <span>{evidenceLocation(evidence)}</span>
        {evidence.role === "CONTRADICTS" && <span className="tag danger">contredit</span>}
      </figcaption>
      <pre>{body}</pre>
    </figure>
  );
}

function DecisionForm({ finding, onDecided }: { finding: Finding; onDecided: () => Promise<void> }) {
  const [mode, setMode] = useState<"REJECT" | "CORRECT" | null>(null);
  const [reason, setReason] = useState("");
  const [explanation, setExplanation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(decision: "ACCEPT" | "REJECT" | "CORRECT") {
    setBusy(true);
    setError("");
    try {
      await api.post(`/findings/${finding.id}/decisions`, {
        decision,
        comment: reason || null,
        corrected_explanation: decision === "CORRECT" ? explanation : null,
      });
      await onDecided();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="decision">
      {error && <p className="error" role="alert">{error}</p>}
      {mode === null && (
        <div className="actions">
          <button className="primary" disabled={busy} onClick={() => send("ACCEPT")}>Accepter</button>
          <button disabled={busy} onClick={() => setMode("REJECT")}>Rejeter</button>
          <button disabled={busy} onClick={() => setMode("CORRECT")}>Corriger</button>
        </div>
      )}
      {mode === "REJECT" && (
        <>
          <label>
            Motif du rejet (obligatoire)
            <textarea autoFocus value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="actions">
            <button className="danger" disabled={busy || !reason.trim()} onClick={() => send("REJECT")}>
              Confirmer le rejet
            </button>
            <button disabled={busy} onClick={() => setMode(null)}>Annuler</button>
          </div>
        </>
      )}
      {mode === "CORRECT" && (
        <>
          <label>
            Explication corrigée
            <textarea autoFocus value={explanation} onChange={(e) => setExplanation(e.target.value)} />
          </label>
          <div className="actions">
            <button className="primary" disabled={busy || !explanation.trim()} onClick={() => send("CORRECT")}>
              Enregistrer la correction
            </button>
            <button disabled={busy} onClick={() => setMode(null)}>Annuler</button>
          </div>
        </>
      )}
    </div>
  );
}

export default function ChangeRequestPage() {
  const id = useParams().id!;
  const [change, setChange] = useState<ChangeRequest | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [diff, setDiff] = useState<RequirementChange[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [tests, setTests] = useState<TestSuggestion[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [linkFilter, setLinkFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const running = run?.status === "QUEUED" || run?.status === "RUNNING";

  const loadRunResults = useCallback(async (latest: Run | undefined) => {
    setRun(latest ?? null);
    if (latest?.status === "COMPLETED") {
      const [f, t] = await Promise.all([
        api.get<Finding[]>(`/analysis-runs/${latest.id}/findings`),
        api.get<TestSuggestion[]>(`/analysis-runs/${latest.id}/test-suggestions`),
      ]);
      setFindings(f);
      setTests(t);
      setSelectedId((current) => {
        if (current !== null && f.some((item) => item.id === current)) return current;
        return f.find((item) => item.status === "PROPOSED")?.id ?? f[0]?.id ?? null;
      });
    }
  }, []);

  const refresh = useCallback(async () => {
    const [c, runs] = await Promise.all([
      api.get<ChangeRequest>(`/change-requests/${id}`),
      api.get<Run[]>(`/change-requests/${id}/analysis-runs`),
    ]);
    setChange(c);
    // Newest completed results stay on screen while a new analysis runs.
    await loadRunResults(runs[0]);
    return c;
  }, [id, loadRunResults]);

  useEffect(() => {
    (async () => {
      const c = await refresh();
      const [d, p] = await Promise.all([
        api.get<RequirementChange[]>(`/change-requests/${id}/diff`),
        api.get<Project>(`/projects/${c.project_id}`),
      ]);
      setDiff(d);
      setProject(p);
    })().catch((e: Error) => setError(e.message));
  }, [id, refresh]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      refresh().catch((e: Error) => setError(e.message));
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, refresh]);

  async function startAnalysis() {
    setStarting(true);
    setError("");
    try {
      await api.post(`/change-requests/${id}/analysis-runs`, {});
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  }

  async function closeRequest() {
    setError("");
    try {
      await api.post(`/change-requests/${id}/close`);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const role = project?.my_role;
  const canAnalyze = role === "ADMIN" || role === "ANALYST";
  const canDecide = role === "ADMIN" || role === "REVIEWER";
  const visible = findings.filter(
    (f) => (!linkFilter || f.link_type === linkFilter) && (!statusFilter || f.status === statusFilter),
  );
  const selected = findings.find((f) => f.id === selectedId) ?? null;
  const changed = diff.filter((item) => item.status !== "UNCHANGED");
  const unchangedCount = diff.length - changed.length;
  const openCount = findings.filter((f) => f.status === "PROPOSED").length;
  const summary = run?.summary as
    | { unresolved_calls?: number; candidates_capped?: number; abstentions?: number; llm?: string }
    | undefined;

  return (
    <>
      <p className="crumbs">
        {change && <Link to={`/projects/${change.project_id}`}>{project?.name ?? "Projet"}</Link>} /
      </p>
      <div className="title-row">
        <div>
          <h1>{change?.title ?? "…"}</h1>
          {change && <span className="tag">{change.status}</span>}
        </div>
        <div className="actions">
          {canAnalyze && change?.status !== "CLOSED" && (
            <button className="primary" disabled={running || starting} onClick={startAnalysis}>
              {running ? "Analyse en cours…" : run ? "Relancer l'analyse" : "Lancer l'analyse"}
            </button>
          )}
          {canAnalyze && change?.status === "REVIEW_COMPLETED" && (
            <button onClick={closeRequest}>Clore la demande</button>
          )}
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {run?.status === "FAILED" && (
        <p className="error" role="alert">Échec de l'analyse : {run.failure_reason}</p>
      )}

      <section>
        <h2>Changements d'exigences</h2>
        {changed.map((item) => (
          <article key={item.req_key} className="change">
            <header>
              <strong>{item.req_key}</strong> <span className="muted">{item.title}</span>
              <span className={`tag status-${item.status.toLowerCase()}`}>{item.status}</span>
            </header>
            {item.status === "MODIFIED" ? (
              <>
                <p className="diff">
                  {item.word_diff.map((part, index) =>
                    part.op === "equal" ? (
                      <span key={index}>{part.text}</span>
                    ) : part.op === "removed" ? (
                      <del key={index}>{part.text}</del>
                    ) : (
                      <ins key={index}>{part.text}</ins>
                    ),
                  )}
                </p>
                <ul className="facts">
                  {item.facts.map((fact, index) => <li key={index}>{factLabel(fact)}</li>)}
                </ul>
              </>
            ) : (
              <p className="diff">{item.new_text ?? item.old_text}</p>
            )}
          </article>
        ))}
        {unchangedCount > 0 && (
          <p className="muted">{unchangedCount} exigence(s) inchangée(s).</p>
        )}
      </section>

      {!run && (
        <section>
          <p className="muted">
            Aucune analyse pour l'instant. {canAnalyze ? "Lancez-la pour obtenir les propositions." : ""}
          </p>
        </section>
      )}

      {run?.status === "COMPLETED" && (
        <section>
          <div className="title-row">
            <h2>File de revue <span className="muted">· {openCount} à traiter sur {findings.length}</span></h2>
            <div className="filters">
              <select aria-label="Filtrer par type de lien" value={linkFilter} onChange={(e) => setLinkFilter(e.target.value)}>
                <option value="">Tous les liens</option>
                <option>EXPLICIT</option>
                <option>STATIC</option>
                <option>INFERRED</option>
                <option>INSUFFICIENT_EVIDENCE</option>
              </select>
              <select aria-label="Filtrer par statut" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">Tous les statuts</option>
                <option>PROPOSED</option>
                <option>ACCEPTED</option>
                <option>REJECTED</option>
                <option>CORRECTED</option>
                <option>SUPERSEDED</option>
              </select>
            </div>
          </div>
          <p className="muted small">
            {summary?.llm} · {summary?.unresolved_calls ?? 0} appels non résolus : du code dépendant peut
            manquer · {summary?.abstentions ?? 0} abstention(s)
            {summary?.candidates_capped ? ` · ${summary.candidates_capped} candidat(s) écarté(s) par le plafond` : ""}
          </p>

          <div className="review">
            <ul className="queue" aria-label="Findings">
              {visible.map((finding) => (
                <li key={finding.id}>
                  <button
                    className={`queue-item ${finding.id === selectedId ? "selected" : ""}`}
                    aria-current={finding.id === selectedId}
                    onClick={() => setSelectedId(finding.id)}
                  >
                    <span className="queue-top">
                      <strong>{finding.req_key}</strong>
                      <span className={`tag state-${finding.status.toLowerCase()}`}>{finding.status}</span>
                    </span>
                    <span className="queue-target">{targetLabel(finding)}</span>
                    <span className="queue-meta">
                      <span className={finding.impact_type === "INCONSISTENCY" ? "tag danger" : "tag"}>
                        {finding.impact_type}
                      </span>
                      <span className="tag">{finding.link_type}</span>
                      {finding.stale && <span className="tag warn">à réanalyser</span>}
                    </span>
                  </button>
                </li>
              ))}
              {visible.length === 0 && <li className="muted pad">Aucun finding pour ce filtre.</li>}
            </ul>

            <div className="detail">
              {!selected && <p className="muted">Sélectionnez un finding.</p>}
              {selected && (
                <>
                  <h3>{targetLabel(selected)}</h3>
                  <p className="meta">
                    <span className="tag">{selected.impact_type}</span>
                    <span className="tag">Lien {selected.link_type}</span>
                    <span className="tag">Support {selected.support}</span>
                    {!selected.llm_analyzed && <span className="tag">non analysé par le LLM</span>}
                  </p>
                  {selected.stale && (
                    <p className="warning">Une version plus récente des sources existe : à réanalyser.</p>
                  )}
                  {selected.evidence_changed && (
                    <p className="warning">Preuves modifiées depuis la dernière décision : à revoir.</p>
                  )}
                  <p>{selected.explanation}</p>
                  {selected.corrected_explanation && (
                    <p className="notice"><strong>Correction : </strong>{selected.corrected_explanation}</p>
                  )}
                  {selected.evidence.map((evidence) => (
                    <EvidenceView key={evidence.id} evidence={evidence} />
                  ))}
                  {selected.status === "PROPOSED" && canDecide && (
                    <DecisionForm key={selected.id} finding={selected} onDecided={async () => { await refresh(); }} />
                  )}
                  {selected.status === "PROPOSED" && !canDecide && (
                    <p className="muted small">Votre rôle ne permet pas de décider.</p>
                  )}
                </>
              )}
            </div>
          </div>
        </section>
      )}

      {tests.length > 0 && (
        <section>
          <h2>Tests suggérés</h2>
          <p className="muted small">Suggestions à écrire : aucun de ces tests n'a été exécuté.</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Exigence</th><th>Entrée</th><th>Résultat attendu</th><th>Justification</th><th>Statut</th></tr>
              </thead>
              <tbody>
                {tests.map((test) => (
                  <tr key={test.id}>
                    <td className="nowrap">{test.req_key}</td>
                    <td className="nowrap">{test.input_value}</td>
                    <td>{test.expected}</td>
                    <td className="muted">{test.rationale}</td>
                    <td><span className="tag">{test.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
