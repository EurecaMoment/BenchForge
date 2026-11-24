type CaptureBudget = {
  scene_id: string; scene_design_id?: string; scene_key?: string;
  limit: number; used: number; remaining: number;
  evidence: {task_id: string; revision: number; token: string; accepted: boolean; captured: boolean}[];
};

/** Browser-safe wire contract; storage and timers remain in the Host. */
export interface ProductionView {
  revision: number;
  active: boolean;
  running: boolean;
  turn: number;
  workflowTurn: number;
  startedAt: number;
  lastTextAt: number;
  lastTextSeq: number;
  lastEventAt: number;
  lastTool: string;
  tools: number;
  shells: number;
  models: string[];
  workspace: string;
  plan: string;
  run: string;
  state: string;
  stage: string;
  error: string;
  review: {review_id: number; stage: string; artifact: string; deadline: number; state: string; revision?: number} | null;
  completed: number;
  total: number;
  delivered: boolean;
  pendingCaptures: {run: string; name: string}[];
  stopReason: string;
  capture_budget?: CaptureBudget | null;
}

type ProductionNotice<K extends string> = {
  kind: K; form: 'notice'; summary: string; noticeKey: string;
  /** Committed domain receipt, never a local timeout prediction. */
  review?: NonNullable<ProductionView['review']>;
  previousReviewId?: number;
};
type CaptureNotice = ProductionNotice<'benchforge/capture-watch'> & {
  capture: {run_id: string; state: string; failed: boolean; blocked?: boolean; error?: string; workflow?: string; capture_budget?: CaptureBudget};
};

declare module '@deepseek-ai/dsh-session-projection/types' {
  interface SessionProjectionMap {
    benchforgeProduction: ProductionView;
  }
}

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    'benchforge/production-progress': ProductionNotice<'benchforge/production-progress'>;
    'benchforge/production-resume': ProductionNotice<'benchforge/production-resume'>;
    'benchforge/production-delivery': ProductionNotice<'benchforge/production-delivery'>;
    'benchforge/production-check-failed': ProductionNotice<'benchforge/production-check-failed'>;
    'benchforge/production-continue': ProductionNotice<'benchforge/production-continue'>;
    'benchforge/capture-watch': CaptureNotice;
    'benchforge/capture-continuation': ProductionNotice<'benchforge/capture-continuation'>;
  }
}
