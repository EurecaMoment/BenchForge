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
  review: {review_id: number; stage: string; artifact: string; deadline: number; state: string} | null;
  completed: number;
  total: number;
  delivered: boolean;
}

type ProductionNotice<K extends string> = {kind: K; form: 'notice'; summary: string; noticeKey: string};
type CaptureNotice = ProductionNotice<'benchforge/capture-watch'> & {
  capture: {run_id: string; state: string; failed: boolean; blocked?: boolean};
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
    'benchforge/capture-watch': CaptureNotice;
    'benchforge/capture-continuation': ProductionNotice<'benchforge/capture-continuation'>;
  }
}
