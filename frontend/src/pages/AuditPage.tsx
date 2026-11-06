import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { AuditEvent } from "../types";

export default function AuditPage() {
  const id = useParams().id!;
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<AuditEvent[]>(`/projects/${id}/audit-events`)
      .then(setEvents)
      .catch((e: Error) => setError(e.message));
  }, [id]);

  return (
    <>
      <p className="crumbs"><Link to={`/projects/${id}`}>Projet</Link> /</p>
      <h1>Historique d'audit</h1>
      <p className="muted">Journal en ajout seul : aucune entrée ne peut être modifiée ni supprimée.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Action</th><th>Acteur</th><th>Détails</th></tr>
          </thead>
          <tbody>
            {events?.map((event) => (
              <tr key={event.id}>
                <td className="nowrap">{new Date(event.created_at).toLocaleString("fr-FR")}</td>
                <td><code>{event.action}</code></td>
                <td>{event.actor_id ?? "système"}</td>
                <td className="muted small">{JSON.stringify(event.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
