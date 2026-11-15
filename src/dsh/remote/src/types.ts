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
