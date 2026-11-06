import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Project } from "../types";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Project[]>("/projects").then(setProjects).catch((e: Error) => setError(e.message));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const project = await api.post<Project>("/projects", { name });
      setProjects((current) => [...(current ?? []), project]);
      setName("");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <>
      <h1>Projets</h1>
      {error && <p className="error" role="alert">{error}</p>}
      {projects === null && !error && <p className="muted">Chargement…</p>}
      {projects?.length === 0 && (
        <p className="muted">Aucun projet pour l'instant. Créez le premier ci-dessous.</p>
      )}
      <ul className="rows">
        {projects?.map((project) => (
          <li key={project.id}>
            <Link to={`/projects/${project.id}`}>{project.name}</Link>
            <span className="tag">{project.my_role}</span>
          </li>
        ))}
      </ul>
      <form className="inline" onSubmit={create}>
        <input
          placeholder="Nom du projet"
          aria-label="Nom du projet"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <button className="primary">Créer</button>
      </form>
    </>
  );
}
