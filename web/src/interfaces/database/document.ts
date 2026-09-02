import { RunningStatus } from '@/constants/knowledge';

export interface IDocumentInfo {
  chunk_num: number;
  create_date: string;
  create_time: number;
  created_by: string;
  nickname: string;
  id: string;
  kb_id: string;
  location: string;
  name: string;
  parser_config: IParserConfig;
  parser_id: string;
  pipeline_id: string;
  pipeline_name: string;
  process_begin_at?: string;
  process_duration: number;
  progress: number;
  progress_msg: string;
  run: RunningStatus;
  size: number;
  source_type: string;
  status: string;
  suffix: string;
  thumbnail: string;
  token_num: number;
  type: string;
  update_date: string;
  update_time: number;
  meta_fields?: Record<string, any> & {
    textbook_kg?: ITextbookKgMetadata;
  };
}

export type TextbookKgStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled';

export interface ITextbookKgResultSummary {
  entity_count?: number;
  relation_count?: number;
  chunk_count?: number;
  book_title?: string;
}

export interface ITextbookKgMetadata {
  job_id: string;
  status: TextbookKgStatus;
  stage?: string;
  progress?: number;
  error?: string | null;
  synced_at?: string;
  result?: ITextbookKgResultSummary;
  graphrag?: {
    status: 'pending' | 'importing' | 'imported' | 'failed';
    error?: string | null;
    artifact_sha256?: string;
    entity_count?: number;
    relation_count?: number;
    imported_at?: string;
    updated_at?: string;
  };
}

export interface ITextbookChapterTreeNode {
  id: string;
  marker: string;
  title: string;
  label: string;
  level: number;
  toc_page_start?: number | null;
  toc_page_end?: number | null;
  pdf_page_start?: number | null;
  pdf_page_end?: number | null;
  content_preview: string;
  content_length: number;
  child_count: number;
  children: ITextbookChapterTreeNode[];
}

export interface ITextbookChapterTree {
  schema_version: 'ragflow-textbook-tree/v1';
  book_title: string;
  toc_pages_pdf: number[];
  node_count: number;
  max_depth: number;
  chapters: ITextbookChapterTreeNode[];
}

export interface IParserConfig {
  delimiter?: string;
  html4excel?: boolean;
  layout_recognize?: boolean;
  pages: any[];
  raptor?: Raptor;
  graphrag?: GraphRag;
}

interface Raptor {
  use_raptor: boolean;
}

interface GraphRag {
  community?: boolean;
  entity_types?: string[];
  method?: string;
  resolution?: boolean;
  use_graphrag?: boolean;
}

export type IDocumentInfoFilter = {
  run_status: Record<number, number>;
  suffix: Record<string, number>;
};
