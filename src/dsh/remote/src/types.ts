type ReadView = 'runtime' | 'summary' | 'progress' | 'run' | 'stage_artifacts' | 'catalog' | 'dataset' |
  'scene_library' | 'scene_selection' | 'stage_review' | 'plan_template' | 'execution_status' | 'synthesis_status' | 'products';

type WriteView = 'stage_review' | 'scene_selection' | 'plan_edit' | 'execution_control' | 'synthesis_start';

export type ReadQuery = {view: ReadView} & Record<string, string>;
export type WriteQuery = {view: WriteView} & Record<string, string>;

export type JsonValue = null | boolean | number | string | JsonValue[] | {[key: string]: JsonValue};

export interface ProductsQuery {
  session_id: string;
  workspace_path: string;
}

export type Stage = 'S1' | 'S2' | 'S3' | 'S4' | 'S5';

export type StageReviewQuery = ProductsQuery;

export interface StageReviewResult {
  state: 'none' | 'waiting_user' | 'paused' | 'needs_input' | 'check_failed' | 'elapsed' | 'continued' | 'superseded';
  can_continue: boolean;
  review_id?: number;
  stage?: Stage;
  artifact?: string;
  deadline?: number;
  server_time?: number;
  session?: string;
  key?: string;
  started?: number;
  instruction?: string | null;
  policy_version?: number;
  revision?: number;
  rounds_required?: number;
  round_number?: number;
  wait_seconds: number;
  remaining_seconds?: number;
  rounds?: JsonValue[];
  check_pending?: boolean;
  last_check?: JsonValue;
  check_failure?: JsonValue;
  checks_completed?: number;
  waits_completed?: number;
  next?: string;
}

export interface ProgressQuery extends ProductsQuery {
  scope?: 'workspace';
}

export interface ProgressResult {
  operations: {id: string; tool: string; state: string; stage: Stage | null;
    session_id?: string; error: string | null; workspace: string; started_at: string | null}[];
  pipelines: JsonValue[];
  scenes: JsonValue;
  current_collection: JsonValue;
  generation: JsonValue;
  parallel_tasks: JsonValue[];
  sources: JsonValue[];
  snapshot_at: string;
  scope: 'explicit_workspace_history' | 'current_conversation_only';
  association: string;
  unassociated_records: number;
}

export interface SceneArtifactsQuery extends ProductsQuery {
  scene: string;
  run?: string;
  task?: string;
  revision?: string;
  library?: '1';
}

export interface SceneArtifactsResult {
  items: JsonValue[];
  [key: string]: JsonValue;
}

export interface ConversationProduct {
  id: string;
  name: string;
  title: string;
  stage: 'S1' | 'S2' | 'S3' | 'S4' | 'S5' | null;
  kind: 'image' | 'video' | 'audio' | 'json' | 'text' | 'file';
  content_type: string;
  source: 'tool' | 'mutation' | 'present';
  session_id: string;
  status: 'available' | 'missing' | 'excluded' | 'unavailable';
  bytes: number | null;
  updated_at: string | null;
  url: string | null;
}

export interface ConversationProducts {
  session_id: string;
  workspace_path: string | null;
  revision: string;
  items: ConversationProduct[];
  total: number;
  truncated: boolean;
}
