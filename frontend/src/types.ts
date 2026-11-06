export type Role = "ADMIN" | "ANALYST" | "REVIEWER" | "VIEWER";

export interface Project {
  id: number;
  name: string;
  description: string;
  my_role: Role | null;
}

export interface DocumentVersion {
  version_id: number;
  name: string;
  number: number;
  extraction_status: string;
  segment_count: number;
  already_imported: boolean;
  message: string | null;
}

export interface Snapshot {
  id: number;
  archive_name: string;
  python_file_count: number;
  parse_error_count: number;
  unresolved_call_count: number;
  excluded_sensitive_count: number;
  warnings: string[];
  already_imported: boolean;
}

export interface RequirementSet {
  id: number;
  number: number;
  source_name: string;
  requirement_count: number;
}

export interface ChangeRequest {
  id: number;
  project_id: number;
  title: string;
  set_before_id: number;
  set_after_id: number;
  status: string;
}

export interface ChangeFact {
  type: string;
  old?: number | string | null;
  new?: number | string | null;
  unit?: string | null;
  old_operator?: string | null;
  operator?: string | null;
  operator_changed?: boolean;
}

export interface RequirementChange {
  req_key: string;
  status: "ADDED" | "REMOVED" | "MODIFIED" | "UNCHANGED";
  title: string;
  old_text: string | null;
  new_text: string | null;
  facts: ChangeFact[];
  word_diff: { op: "equal" | "removed" | "added"; text: string }[];
}

export interface Run {
  id: number;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  summary: Record<string, unknown>;
  failure_reason: string | null;
  duration_ms: number | null;
  llm_model: string | null;
  inputs: { input_type: string; version_id: number }[];
}

export interface Evidence {
  id: number;
  source_type: "SEGMENT" | "SYMBOL" | "REQUIREMENT";
  role: "SUPPORTS" | "CONTRADICTS";
  quote: string;
  document_name: string | null;
  section_path: string | null;
  page: number | null;
  file_path: string | null;
  start_line: number | null;
  end_line: number | null;
  context: string;
  context_start_line: number | null;
}

export type FindingStatus = "PROPOSED" | "ACCEPTED" | "REJECTED" | "CORRECTED" | "SUPERSEDED";

export interface Finding {
  id: number;
  req_key: string;
  status: FindingStatus;
  stale: boolean;
  evidence_changed: boolean;
  llm_analyzed: boolean;
  target_type: string;
  target_key: string;
  target: Record<string, unknown>;
  impact_type: string;
  link_type: string;
  support: string;
  explanation: string;
  corrected_target: string | null;
  corrected_explanation: string | null;
  evidence: Evidence[];
}

export interface TestSuggestion {
  id: number;
  req_key: string;
  input_value: string;
  expected: string;
  rationale: string;
  origin: string;
  status: string;
}

export interface AuditEvent {
  id: number;
  actor_id: number | null;
  action: string;
  entity_type: string | null;
  entity_id: number | null;
  details: Record<string, unknown>;
  created_at: string;
}
